"""Recent-transcriptions browser.

Lists rows from :mod:`core.history` for ``user_id="desktop"`` and lets the
user pop any of them back into the Transcriptor window without
re-transcribing — segments are already on disk in ``history.db``.

The window owns no business logic: it asks ``core.history`` for the rows,
renders one button per row, and on click hands the loaded segments back
to the caller through ``on_open``. ``main.App`` is responsible for
opening (or focusing) the Transcriptor window and calling
:meth:`TranscriptorWindow.restore_from_history`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk

from core import Segment, history

logger = logging.getLogger(__name__)


def format_history_label(row: dict) -> str:
    """One human-readable line per history row.

    Example: ``"2026-05-11 18:42  ·  📎 audio.mp3"``. Bad/missing dates
    fall through as the raw string so we never lose the source label.
    """
    raw = (row.get("created_at") or "").strip()
    pretty = raw
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            pretty = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    label = (row.get("label") or "").strip() or "(без названия)"
    if pretty:
        return f"{pretty}  ·  {label}"
    return label


class HistoryWindow(ctk.CTkToplevel):
    """Shows recent transcriptions; click → ``on_open(row, segments)``."""

    USER_ID = "desktop"
    LIMIT = 50

    def __init__(
        self,
        master,
        *,
        on_open: Callable[[dict, list[Segment]], None],
    ):
        super().__init__(master)
        self.title("VoiceType Studio — История")
        self.geometry("680x520")
        self.minsize(480, 320)

        self._on_open = on_open
        self._row_buttons: list[ctk.CTkButton] = []

        # --- top bar ------------------------------------------------
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))

        ctk.CTkLabel(
            top,
            text="Последние транскрипции",
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            top, text="Обновить", width=100, command=self.refresh,
        ).pack(side="right")

        # --- list ---------------------------------------------------
        self._list = ctk.CTkScrollableFrame(self)
        self._list.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self._empty_hint = ctk.CTkLabel(
            self._list,
            text=(
                "История пуста. Транскрибируйте файл или ссылку в окне "
                "Транскриптор — записи появятся здесь."
            ),
            wraplength=540,
            justify="center",
        )

        self.refresh()

    # ----- public API ---------------------------------------------------

    def refresh(self) -> None:
        for btn in self._row_buttons:
            try:
                btn.destroy()
            except Exception:
                pass
        self._row_buttons.clear()
        try:
            self._empty_hint.pack_forget()
        except Exception:
            pass

        try:
            rows = history.recent(self.USER_ID, limit=self.LIMIT)
        except Exception:
            logger.exception("Failed to load history")
            rows = []

        if not rows:
            self._empty_hint.pack(pady=40)
            return

        for row in rows:
            btn = ctk.CTkButton(
                self._list,
                text=format_history_label(row),
                anchor="w",
                height=36,
                command=lambda r=row: self._open_row(r),
            )
            btn.pack(fill="x", padx=2, pady=2)
            self._row_buttons.append(btn)

    # ----- internal -----------------------------------------------------

    def _open_row(self, row: dict) -> None:
        row_id = row.get("id")
        if row_id is None:
            return
        try:
            segments = history.get_segments(int(row_id), self.USER_ID)
        except Exception:
            logger.exception("Failed to load segments for history id=%s", row_id)
            return
        if not segments:
            return
        try:
            self._on_open(row, segments)
        except Exception:
            logger.exception("on_open callback raised")
            return
        # Library → editor flow: close after opening so the desktop isn't
        # cluttered with two windows showing the same content. Defer so
        # CTkToplevel's internal ``self.after(...)`` focus calls don't
        # fire on a destroyed widget (TclError ".!historywindow").
        self._safe_destroy_later(250)

    def _safe_destroy_later(self, delay_ms: int) -> None:
        def _destroy_if_alive() -> None:
            try:
                if self.winfo_exists():
                    self.destroy()
            except Exception:
                pass

        try:
            self.after(delay_ms, _destroy_if_alive)
        except Exception:
            _destroy_if_alive()


def open_history_window(
    master,
    *,
    on_open: Callable[[dict, list[Segment]], None],
) -> HistoryWindow:
    """Construct, raise to the front and return the window."""
    win = HistoryWindow(master, on_open=on_open)

    # CTkToplevel schedules its own ``after(...)`` to grab focus shortly
    # after construction; calling ``focus_force()`` synchronously here
    # races with that and sometimes throws TclError. Defer ours too.
    def _raise() -> None:
        try:
            if win.winfo_exists():
                win.lift()
                win.focus_force()
        except Exception:
            pass

    try:
        win.after(150, _raise)
    except Exception:
        _raise()
    return win
