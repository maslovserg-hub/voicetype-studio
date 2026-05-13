"""One MessageWidget per transcription task — chat-style.

Three states share the same frame:

* ``processing`` — a progress bar + status label;
* ``done`` — a row of eight format buttons. Clicking one swaps in the
  result row (text area + Copy / Save / Clear buttons, or, for 🔊, a
  player line);
* ``error`` — red label with the exception message.

Each widget can also be **collapsed** to a single header line (the
TranscriptorWindow auto-collapses older bubbles when a new task arrives
so the user isn't drowning in eight-button rows). Click the header /
arrow to expand again.

The widget owns no thread state. The window's drain loop calls
:meth:`set_progress`, :meth:`mark_done`, :meth:`mark_error` from the Tk
thread; format-button clicks return through callbacks supplied at
construction.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Literal, Optional

import customtkinter as ctk

from ._clipboard_menu import attach_clipboard_menu
from ._format_dispatch import (
    FORMAT_LABELS,
    FormatResult,
    LLM_FORMATS,
    TEXT_FORMATS,
    TTS_FORMAT,
    file_extension_for,
)

logger = logging.getLogger(__name__)


# Two visual rows of buttons: deterministic formats first, AI second.
_BUTTON_ROWS: tuple[tuple[str, ...], ...] = (
    TEXT_FORMATS,
    LLM_FORMATS + (TTS_FORMAT,),
)

WidgetState = Literal["processing", "done", "error"]


class MessageWidget(ctk.CTkFrame):
    """A single chat bubble for one TranscriptionTask."""

    def __init__(
        self,
        master,
        *,
        task_id: str,
        source_label: str,
        on_format_click: Callable[[str, str], None],
        # ^ (task_id, format_key) → caller dispatches deliver_format
    ):
        super().__init__(master, corner_radius=12)
        self.task_id = task_id
        self._on_format_click = on_format_click

        self._format_buttons: dict[str, ctk.CTkButton] = {}
        self._busy_format: str | None = None  # which button is "Готовлю…"

        # State the collapse/expand machinery needs to re-pack correctly.
        self._state: WidgetState = "processing"
        self._collapsed: bool = False

        # --- header (toggle arrow + label, both clickable) -----------
        self._header_row = ctk.CTkFrame(self, fg_color="transparent")
        self._header_row.pack(fill="x", padx=8, pady=(6, 4))

        self._toggle_btn = ctk.CTkButton(
            self._header_row,
            text="▼",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="transparent",
            hover_color=("#dadada", "#3a3a3a"),
            text_color=("#222", "#ddd"),
            command=self.toggle,
        )
        self._toggle_btn.pack(side="left")

        self._header = ctk.CTkLabel(
            self._header_row,
            text=source_label,
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
            cursor="hand2",
        )
        self._header.pack(side="left", fill="x", expand=True, padx=(6, 0))
        # Clicking the label itself also toggles — bigger hit-target than
        # the small arrow button.
        self._header.bind("<Button-1>", lambda _e: self.toggle())

        # --- progress row (visible during processing) ----------------
        self._progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._progress_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._status = ctk.CTkLabel(
            self._progress_frame,
            text="В очереди…",
            anchor="w",
            font=ctk.CTkFont(size=11),
        )
        self._status.pack(fill="x")

        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.set(0.0)
        self._progress_bar.pack(fill="x", pady=(4, 0))

        # --- format buttons (visible after transcription) ------------
        self._buttons_frame = ctk.CTkFrame(self, fg_color="transparent")
        for row_keys in _BUTTON_ROWS:
            row = ctk.CTkFrame(self._buttons_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            for key in row_keys:
                btn = ctk.CTkButton(
                    row,
                    text=FORMAT_LABELS[key],
                    width=120,
                    command=lambda k=key: self._handle_click(k),
                )
                btn.pack(side="left", padx=2)
                self._format_buttons[key] = btn

        # --- result row (created lazily once a format is delivered) --
        self._result_frame: ctk.CTkFrame | None = None
        self._result_text: ctk.CTkTextbox | None = None
        self._last_result: FormatResult | None = None
        self._last_format: str | None = None

        # --- error row (created lazily) ------------------------------
        self._error_label: ctk.CTkLabel | None = None

    # ----- collapse / expand --------------------------------------------

    def is_collapsed(self) -> bool:
        return self._collapsed

    def collapse(self) -> None:
        """Hide everything except the header row."""
        if self._collapsed:
            return
        self._collapsed = True
        for w in (
            self._progress_frame,
            self._buttons_frame,
            self._result_frame,
            self._error_label,
        ):
            if w is None:
                continue
            try:
                w.pack_forget()
            except Exception:
                pass
        try:
            self._toggle_btn.configure(text="▶")
        except Exception:
            pass

    def expand(self) -> None:
        """Restore the body for the current ``_state``."""
        if not self._collapsed:
            return
        self._collapsed = False
        try:
            self._toggle_btn.configure(text="▼")
        except Exception:
            pass
        if self._state == "processing":
            self._progress_frame.pack(fill="x", padx=12, pady=(0, 8))
        elif self._state == "done":
            self._buttons_frame.pack(fill="x", padx=8, pady=(0, 8))
            if self._result_frame is not None:
                self._result_frame.pack(
                    fill="both", expand=True, padx=12, pady=(0, 8)
                )
        elif self._state == "error" and self._error_label is not None:
            self._error_label.pack(fill="x", padx=12, pady=(0, 8))

    def toggle(self) -> None:
        if self._collapsed:
            self.expand()
        else:
            self.collapse()

    # ----- progress / completion API ------------------------------------

    def set_progress(self, status_text: str, percent: int) -> None:
        self._status.configure(text=status_text)
        self._progress_bar.set(max(0.0, min(1.0, percent / 100.0)))

    def mark_done(self) -> None:
        """Hide progress, reveal format buttons."""
        self._state = "done"
        try:
            self._progress_frame.pack_forget()
        except Exception:
            pass
        if not self._collapsed:
            self._buttons_frame.pack(fill="x", padx=8, pady=(0, 8))

    def mark_error(self, message: str) -> None:
        self._state = "error"
        try:
            self._progress_frame.pack_forget()
        except Exception:
            pass
        if self._error_label is None:
            self._error_label = ctk.CTkLabel(
                self,
                text="",
                anchor="w",
                wraplength=700,
                justify="left",
                text_color="#ff6b6b",
            )
        self._error_label.configure(text=f"⚠ Ошибка: {message}")
        if not self._collapsed:
            self._error_label.pack(fill="x", padx=12, pady=(0, 8))

    # ----- format-button result wiring -----------------------------------

    # Per-format button-busy labels — surface what's actually happening
    # during a long-running synthesis or LLM call.
    _BUSY_LABELS: dict = {
        "tts": "🔊 Озвучиваю… (10–30 сек)",
        "brief": "📋 Считаю тезисы…",
        "structured": "📚 Структурирую…",
        "roles": "🎭 Размечаю по ролям…",
        "questions": "❓ Ищу вопросы…",
    }

    def set_format_busy(self, format_key: str) -> None:
        self._busy_format = format_key
        btn = self._format_buttons.get(format_key)
        if btn is not None:
            label = self._BUSY_LABELS.get(format_key, "Готовлю…")
            btn.configure(state="disabled", text=label)

    def show_format_result(self, format_key: str, result: FormatResult) -> None:
        """Replace any prior result row with the new one."""
        # Restore the button label.
        btn = self._format_buttons.get(format_key)
        if btn is not None:
            btn.configure(state="normal", text=FORMAT_LABELS[format_key])
        self._busy_format = None

        # Tear down any previous result frame.
        if self._result_frame is not None:
            try:
                self._result_frame.destroy()
            except Exception:
                pass

        self._last_result = result
        self._last_format = format_key
        self._result_frame = ctk.CTkFrame(self, fg_color="transparent")
        if not self._collapsed:
            self._result_frame.pack(
                fill="both", expand=True, padx=12, pady=(0, 8)
            )

        if result.kind == "text":
            self._render_text(self._result_frame, result.content)
        else:
            self._render_audio(
                self._result_frame, result.content, result.preview_text or ""
            )

    def show_format_error(self, format_key: str, message: str) -> None:
        btn = self._format_buttons.get(format_key)
        if btn is not None:
            btn.configure(state="normal", text=FORMAT_LABELS[format_key])
        self._busy_format = None

        if self._result_frame is not None:
            try:
                self._result_frame.destroy()
            except Exception:
                pass
        self._result_frame = ctk.CTkFrame(self, fg_color="transparent")
        if not self._collapsed:
            self._result_frame.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(
            self._result_frame,
            text=f"⚠ {FORMAT_LABELS.get(format_key, format_key)}: {message}",
            anchor="w",
            wraplength=700,
            justify="left",
            text_color="#ff6b6b",
        ).pack(fill="x")

    # ----- internal helpers ---------------------------------------------

    def _handle_click(self, format_key: str) -> None:
        if self._busy_format is not None:
            return  # ignore clicks while one is in flight
        try:
            self._on_format_click(self.task_id, format_key)
        except Exception:
            logger.exception("on_format_click raised for %s", format_key)

    def _render_text(self, parent: ctk.CTkFrame, text: str) -> None:
        textbox = ctk.CTkTextbox(parent, height=200, wrap="word")
        _insert_with_markdown_bold(textbox, text)
        textbox.configure(state="normal")  # leave editable for selection
        textbox.pack(fill="both", expand=True)
        # Right-click → Copy / Select All. No "Paste" — this is a result view.
        attach_clipboard_menu(textbox, paste=False, cut=False)
        self._result_text = textbox

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(
            actions, text="Скопировать", width=110,
            command=lambda: self._copy_to_clipboard(text),
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            actions, text="Сохранить как…", width=130,
            command=lambda: self._save_as(text),
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            actions, text="Очистить", width=90,
            command=self._clear_result,
        ).pack(side="left", padx=2)

    def _render_audio(
        self,
        parent: ctk.CTkFrame,
        wav_path: str,
        preview_text: str,
    ) -> None:
        # Header with play button right-aligned for prominence — without
        # this users were getting stuck at "preparing…" state, missing that
        # synthesis already finished and they just need to click play.
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text=f"🔊 Озвучка готова — {Path(wav_path).name}",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            header,
            text="▶ Воспроизвести",
            width=160,
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: _open_in_default_player(wav_path),
        ).pack(side="right")

        if preview_text:
            preview = ctk.CTkTextbox(parent, height=100, wrap="word")
            preview.insert("0.0", preview_text)
            preview.pack(fill="both", expand=True, pady=(6, 0))
            attach_clipboard_menu(preview, paste=False, cut=False)

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(
            actions, text="Сохранить как…", width=130,
            command=lambda: self._save_audio_as(wav_path),
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            actions, text="Открыть папку", width=130,
            command=lambda: _open_containing_folder(wav_path),
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            actions, text="Очистить", width=90,
            command=self._clear_result,
        ).pack(side="left", padx=2)

    def _clear_result(self) -> None:
        if self._result_frame is not None:
            try:
                self._result_frame.destroy()
            except Exception:
                pass
            self._result_frame = None
            self._result_text = None
            self._last_result = None
            self._last_format = None

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            import pyperclip

            pyperclip.copy(text)
        except Exception:
            logger.exception("Clipboard copy failed")

    def _save_as(self, text: str) -> None:
        from tkinter import filedialog

        ext = file_extension_for(self._last_format or "text")
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[("Text", "*.txt"), ("SRT", "*.srt"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except Exception:
            logger.exception("Failed to save text result to %s", path)

    def _save_audio_as(self, source_wav: str) -> None:
        from shutil import copyfile
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            copyfile(source_wav, path)
        except Exception:
            logger.exception("Failed to copy audio %s -> %s", source_wav, path)


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)


def _insert_with_markdown_bold(textbox: "ctk.CTkTextbox", text: str) -> None:
    """Insert ``text`` into ``textbox`` rendering ``**word**`` as bold.

    The STRUCTURED prompt emits Markdown bold around key terms. CTkTextbox
    is just a Tk Text widget under the hood, so we use a tag with the same
    font but ``weight="bold"`` and split the string at each ``**…**`` span.
    Headings (``## …``) and other markdown are left as plain text — only
    bold needs visual treatment for now.
    """
    # Configure the tag once. ``CTkTextbox.tag_config`` proxies to the
    # underlying Tk widget. Use the textbox's current font to inherit size
    # / family — we only flip ``weight``.
    try:
        base_font = textbox.cget("font")
    except Exception:
        base_font = None
    # Tk accepts a (family, size, *styles) tuple; fall back to a simple
    # weight-only spec when we can't introspect.
    if isinstance(base_font, tuple) and len(base_font) >= 2:
        bold_font = (base_font[0], base_font[1], "bold")
    else:
        bold_font = ("TkDefaultFont", 11, "bold")
    try:
        textbox.tag_config("md_bold", font=bold_font)
    except Exception:
        # Fallback: no bold rendering, but still insert text cleanly.
        textbox.insert("0.0", text)
        return

    cursor = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > cursor:
            textbox.insert("end", text[cursor : m.start()])
        textbox.insert("end", m.group(1), ("md_bold",))
        cursor = m.end()
    if cursor < len(text):
        textbox.insert("end", text[cursor:])


def _open_in_default_player(path: str) -> None:
    """Best-effort: open the file in the OS default app."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: SIM115 — Windows-only API
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:
        logger.exception("Failed to open %s", path)


def _open_containing_folder(path: str) -> None:
    """Open the folder that contains ``path`` and select the file (Windows)."""
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-R", str(path)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
    except Exception:
        logger.exception("Failed to open folder for %s", path)
