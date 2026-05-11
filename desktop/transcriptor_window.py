"""Chat-style transcription window — the main user-facing surface.

Top section: a scrollable feed of :class:`MessageWidget` bubbles, one per
submitted task. Bottom section: an :class:`InputBar` with file-picker, URL
field, Старт button and a drop zone overlay (tkinterdnd2).

Threading
---------
* The window runs on the Tk thread.
* Each task dispatches its download/convert/transcribe coroutine to the
  asyncio loop running in a daemon thread (``self.bot_loop``) via
  :func:`asyncio.run_coroutine_threadsafe`.
* Progress events come back through a private :class:`queue.Queue` polled
  every 50 ms by ``self.master.after``. The queue is window-private — bot
  and dictation use other channels.

Closing
-------
The window's "X" hides it; the app keeps running in the tray. ``destroy()``
is wired only to app shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional
from urllib.parse import urlparse

import customtkinter as ctk

from core import (
    AudioConverter,
    Downloader,
    Segment,
    Transcriber,
    history,
)
from core.settings import Settings

from ._clipboard_menu import attach_clipboard_menu
from ._format_dispatch import FormatResult, deliver_format
from ._icons import as_ctk_image, make_attach_icon
from ._message_widget import MessageWidget

logger = logging.getLogger(__name__)


TaskStatus = Literal[
    "queued", "downloading", "converting", "transcribing", "done", "error"
]


@dataclass
class TranscriptionTask:
    task_id: str
    source_label: str
    source: str  # path or url, as user supplied
    status: TaskStatus = "queued"
    progress: int = 0
    segments: Optional[list[Segment]] = None
    widget: Optional[MessageWidget] = None
    error: Optional[str] = None


# --- input parsing helpers (pure, easy to unit-test) ---------------------

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def classify_input(value: str) -> tuple[str, str]:
    """Tell whether the user typed a URL, a file path, or rubbish.

    Returns ``(kind, normalized)`` where ``kind`` is one of ``"url"``,
    ``"file"``, or ``"invalid"``. Path existence is checked — a typed
    path that doesn't exist is treated as invalid (vs. a URL we trust
    yt-dlp / Yandex.Disk to validate later).
    """
    s = (value or "").strip().strip('"')
    if not s:
        return "invalid", ""
    if _URL_RE.match(s):
        return "url", s
    p = Path(s)
    if p.is_file():
        return "file", str(p)
    return "invalid", s


def label_for_source(kind: str, value: str) -> str:
    """Human-readable header text for a MessageWidget."""
    if kind == "file":
        return f"📎 {Path(value).name}"
    if kind == "url":
        host = (urlparse(value).hostname or "").lower()
        if "yandex" in host or "yadi.sk" in host:
            return "🔗 Я.Диск"
        if "youtube" in host or "youtu.be" in host:
            return "🔗 YouTube"
        if "rutube" in host:
            return "🔗 RuTube"
        if "vk." in host or "vkvideo" in host:
            return "🔗 VK"
        if "drive.google" in host:
            return "🔗 Google Drive"
        return f"🔗 {host or value[:40]}"
    return value[:60]


# --- input bar -----------------------------------------------------------


class InputBar(ctk.CTkFrame):
    """Bottom row: 📎 + URL entry + Старт."""

    def __init__(
        self,
        master,
        *,
        on_submit_url: Callable[[str], None],
        on_pick_file: Callable[[], None],
    ):
        super().__init__(master)
        self._on_submit_url = on_submit_url

        # Hand-drawn icon — emoji glyphs render unreliably across Windows
        # font fallbacks, so we ship our own.
        self._attach_icon = as_ctk_image(make_attach_icon(size=22), size=22)
        self._attach_btn = ctk.CTkButton(
            self,
            text="Файл",
            image=self._attach_icon,
            compound="left",
            width=84,
            command=on_pick_file,
        )
        self._attach_btn.pack(side="left", padx=(8, 4), pady=8)

        self._entry = ctk.CTkEntry(
            self,
            placeholder_text="Ссылка или перетащите файл сюда",
        )
        self._entry.pack(side="left", fill="x", expand=True, padx=4, pady=8)
        self._entry.bind("<Return>", lambda _e: self._fire_submit())
        attach_clipboard_menu(self._entry)

        self._submit_btn = ctk.CTkButton(
            self,
            text="Старт",
            width=90,
            command=self._fire_submit,
        )
        self._submit_btn.pack(side="left", padx=(4, 8), pady=8)

    def _fire_submit(self) -> None:
        value = self._entry.get()
        if not value.strip():
            return
        self._on_submit_url(value)
        self._entry.delete(0, "end")


# --- main window ---------------------------------------------------------


class TranscriptorWindow(ctk.CTkToplevel):
    """Chat-style window for file/URL transcriptions."""

    DESKTOP_USER_ID = "desktop"

    def __init__(
        self,
        master,
        *,
        bot_loop: asyncio.AbstractEventLoop,
        asr_executor: Any,  # ThreadPoolExecutor — not strictly required here
        settings: Settings,
    ):
        super().__init__(master)
        self.title("VoiceType Studio — Транскриптор")
        self.geometry("900x700")
        self.minsize(640, 480)

        self.bot_loop = bot_loop
        self.asr_executor = asr_executor
        self.settings = settings

        self._tasks: dict[str, TranscriptionTask] = {}
        self._event_queue: "queue.Queue[tuple]" = queue.Queue()

        # --- feed ----------------------------------------------------
        self._feed = ctk.CTkScrollableFrame(self)
        self._feed.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self._empty_hint = ctk.CTkLabel(
            self._feed,
            text=(
                "Перетащите файл сюда или вставьте ссылку (Я.Диск / YouTube /"
                " RuTube / VK / Google Drive). За раз — один файл."
            ),
            wraplength=700,
            justify="center",
        )
        self._empty_hint.pack(pady=40)

        # --- input bar ----------------------------------------------
        self._input = InputBar(
            self,
            on_submit_url=self._submit_value,
            on_pick_file=self._open_file_picker,
        )
        self._input.pack(fill="x", padx=10, pady=10)

        # --- drag-and-drop binding (tkinterdnd2 wrapping the root) --
        self._wire_drag_and_drop()

        # --- close → hide instead of destroy ------------------------
        self.protocol("WM_DELETE_WINDOW", self.hide)

        # --- start polling the cross-thread queue -------------------
        self.after(50, self._drain_event_queue)

    # ----- public API used by main.py -----------------------------------

    def hide(self) -> None:
        self.withdraw()

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def submit_external(self, source: str) -> None:
        """Programmatic entry-point — same as if the user typed in the bar."""
        self._submit_value(source)

    def restore_from_history(
        self,
        *,
        source_label: str,
        source: str,
        segments: list[Segment],
    ) -> None:
        """Re-create a finished bubble from a stored history row.

        No download / convert / transcribe — segments are already on disk.
        The bubble appears on top, expanded; format buttons work as usual.
        """
        task = TranscriptionTask(
            task_id=uuid.uuid4().hex[:10],
            source_label=source_label,
            source=source,
            status="done",
            progress=100,
            segments=segments,
        )
        self._tasks[task.task_id] = task
        self._spawn_widget(task)
        # The widget starts in "processing" state; flip it straight to
        # "done" so the format buttons are visible.
        if task.widget is not None:
            task.widget.mark_done()

    # ----- input dispatch ----------------------------------------------

    def _open_file_picker(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Выберите аудио или видео",
            filetypes=[
                ("Медиафайлы", "*.mp3 *.mp4 *.wav *.m4a *.ogg *.webm *.mkv *.flac"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self._submit_value(path)

    def _submit_value(self, value: str) -> None:
        kind, normalized = classify_input(value)
        if kind == "invalid":
            self._show_toast("Не похоже на ссылку или существующий файл.")
            return
        self._dispatch_task(label_for_source(kind, normalized), normalized)

    def _dispatch_task(self, source_label: str, source: str) -> None:
        task = TranscriptionTask(
            task_id=uuid.uuid4().hex[:10],
            source_label=source_label,
            source=source,
        )
        self._tasks[task.task_id] = task
        self._spawn_widget(task)
        asyncio.run_coroutine_threadsafe(
            self._run_task(task), self.bot_loop
        )

    def _spawn_widget(self, task: TranscriptionTask) -> None:
        try:
            self._empty_hint.pack_forget()
        except Exception:
            pass

        # Auto-collapse previously-finished bubbles so the feed stays
        # readable when many tasks are queued. The newest one will be the
        # only one expanded after this.
        for existing in list(self._tasks.values()):
            if existing.task_id == task.task_id or existing.widget is None:
                continue
            try:
                existing.widget.collapse()
            except Exception:
                pass

        widget = MessageWidget(
            self._feed,
            task_id=task.task_id,
            source_label=task.source_label,
            on_format_click=self._on_format_click,
        )
        # New bubble goes on TOP, before any existing ones. Without
        # ``before=`` pack appends at the bottom which forces the user to
        # scroll — easy to miss when several files are queued.
        existing_widgets = [
            t.widget
            for t in self._tasks.values()
            if t.widget is not None and t.task_id != task.task_id
        ]
        if existing_widgets:
            widget.pack(fill="x", pady=6, before=existing_widgets[0])
        else:
            widget.pack(fill="x", pady=6)
        task.widget = widget

        # Scroll to top — newest bubble is up there now.
        try:
            self._feed._parent_canvas.yview_moveto(0.0)
        except Exception:
            pass

    # ----- async pipeline ----------------------------------------------

    async def _run_task(self, task: TranscriptionTask) -> None:
        downloaded_path: Optional[Path] = None  # only set for URL inputs
        wav_path: Optional[Path] = None
        chunks_dir: Optional[Path] = None
        try:
            self._post(("progress", task.task_id, "Скачиваю…", 5))
            if _looks_like_url(task.source):
                # yt-dlp fires this from its worker thread; ``_post`` is
                # thread-safe (queue.Queue) so the GUI bar updates live
                # instead of sitting at 5% for the whole download. Map
                # 0–100% from yt-dlp into 5–25% of our overall pipeline.
                def dl_cb(percent: int, status: str, _tid=task.task_id) -> None:
                    mapped = max(5, min(25, 5 + int(percent * 0.20)))
                    self._post(("progress", _tid, status, mapped))

                downloaded_path, _src_type = await Downloader.download(
                    task.source, progress_callback=dl_cb,
                )
                file_path = downloaded_path
            else:
                file_path = Path(task.source)

            self._post(("progress", task.task_id, "Конвертирую в WAV…", 25))
            wav_path = await AudioConverter.to_wav(Path(file_path))

            self._post(("progress", task.task_id, "Транскрибирую…", 30))

            def cb(p: int) -> None:
                self._post((
                    "progress", task.task_id,
                    f"Транскрибирую {p}%…", 30 + int(p * 0.7),
                ))

            segments = await Transcriber.transcribe(
                Path(wav_path), progress_callback=cb,
            )
            # ``split_for_short_asr`` writes its slices next to the wav.
            chunks_dir = wav_path.parent / f"{wav_path.stem}_short_chunks"

            try:
                history.add(
                    user_id=self.DESKTOP_USER_ID,
                    source_label=task.source_label,
                    source=str(task.source),
                    segments=segments,
                )
            except Exception:
                logger.exception("Failed to write history row for task %s", task.task_id)

            self._post(("done", task.task_id, segments))
        except Exception as e:
            logger.exception("Task %s failed", task.task_id)
            self._post(("error", task.task_id, str(e)))
        finally:
            # Clean up temp files. Without this, every transcription leaves
            # ~the size of the source video in ``data/tmp/`` and a few
            # hundred chunk WAVs alongside. ``downloaded_path`` is None for
            # local files — never delete the user's own input.
            self._cleanup_temp_files(downloaded_path, wav_path, chunks_dir)

    def _cleanup_temp_files(
        self,
        downloaded_path: Optional[Path],
        wav_path: Optional[Path],
        chunks_dir: Optional[Path],
    ) -> None:
        import shutil

        for p in (downloaded_path, wav_path):
            if p is None:
                continue
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                logger.debug("could not remove %s", p, exc_info=True)
        if chunks_dir and chunks_dir.exists():
            try:
                shutil.rmtree(chunks_dir, ignore_errors=True)
            except OSError:
                logger.debug("could not remove %s", chunks_dir, exc_info=True)

    async def _run_format(self, task_id: str, format_key: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.segments is None:
            return
        try:
            result = await deliver_format(task.segments, format_key, self.settings)
            self._post(("format_done", task_id, format_key, result))
        except Exception as e:
            logger.exception(
                "Format %s failed for task %s", format_key, task_id
            )
            self._post(("format_error", task_id, format_key, str(e)))

    # ----- format-button handler (Tk thread) ---------------------------

    def _on_format_click(self, task_id: str, format_key: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.widget is None or task.segments is None:
            return
        task.widget.set_format_busy(format_key)
        asyncio.run_coroutine_threadsafe(
            self._run_format(task_id, format_key), self.bot_loop,
        )

    # ----- cross-thread queue ------------------------------------------

    def _post(self, event: tuple) -> None:
        """Called from the asyncio loop's thread; safe via Queue."""
        self._event_queue.put(event)

    def _drain_event_queue(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        # Reschedule.
        try:
            self.after(50, self._drain_event_queue)
        except Exception:
            pass

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, task_id, status_text, percent = event
            task = self._tasks.get(task_id)
            if task and task.widget:
                task.widget.set_progress(status_text, percent)
                task.progress = percent
        elif kind == "done":
            _, task_id, segments = event
            task = self._tasks.get(task_id)
            if task and task.widget:
                task.segments = segments
                task.status = "done"
                task.widget.mark_done()
        elif kind == "error":
            _, task_id, message = event
            task = self._tasks.get(task_id)
            if task and task.widget:
                task.status = "error"
                task.error = message
                task.widget.mark_error(message)
        elif kind == "format_done":
            _, task_id, format_key, result = event
            task = self._tasks.get(task_id)
            if task and task.widget:
                task.widget.show_format_result(format_key, result)
        elif kind == "format_error":
            _, task_id, format_key, message = event
            task = self._tasks.get(task_id)
            if task and task.widget:
                task.widget.show_format_error(format_key, message)
        else:
            logger.warning("Unknown event kind: %r", kind)

    # ----- drag and drop -----------------------------------------------

    def _wire_drag_and_drop(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES  # noqa: F401
        except Exception:
            logger.warning("tkinterdnd2 not available — drag&drop disabled")
            return

        try:
            self.drop_target_register("DND_Files")
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            logger.exception(
                "Window not DnD-aware — main.py must use TkinterDnD-wrapped root"
            )

    def _on_drop(self, event: Any) -> None:
        files = _parse_dropped_paths(event.data)
        if len(files) != 1:
            self._show_toast("Принимаем только 1 файл за раз.")
            return
        self._submit_value(files[0])

    # ----- toast --------------------------------------------------------

    def _show_toast(self, message: str) -> None:
        """Tiny inline notification — top of the feed for ~3 seconds."""
        toast = ctk.CTkLabel(
            self._feed,
            text=message,
            fg_color=("#ffe2a8", "#553300"),
            corner_radius=6,
            anchor="center",
            pady=6,
        )
        toast.pack(fill="x", pady=4)

        def _drop():
            try:
                toast.destroy()
            except Exception:
                pass

        self.after(3000, _drop)


# --- helpers ---------------------------------------------------------------


def _looks_like_url(value: str) -> bool:
    return bool(_URL_RE.match(value or ""))


_DND_BRACED_RE = re.compile(r"\{([^}]+)\}|(\S+)")


def _parse_dropped_paths(data: str) -> list[str]:
    """tkinterdnd2 wraps paths with spaces in ``{...}`` braces."""
    out: list[str] = []
    for m in _DND_BRACED_RE.finditer(data or ""):
        path = m.group(1) or m.group(2)
        if path:
            out.append(path)
    return out
