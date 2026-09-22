"""History side panel inside the Transcriptor window.

Two tabs:

* «Расшифровки» — rows from :func:`core.history.recent`; click pops the
  stored segments back into the feed through ``on_open`` (no re-run).
* «Скачанное» — rows from :func:`core.history.recent_downloads`: file
  name, the source link (click opens it in the browser, «Ссылка» copies
  it) and «Папка», which reveals the file in Explorer.

The panel owns no business logic: it asks ``core.history`` for rows and
hands transcriptions back to the Transcriptor via ``on_open``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from core import Segment, Settings, history
from core.history import owner_scope

logger = logging.getLogger(__name__)

TAB_TRANSCRIPTS = "Расшифровки"
TAB_DOWNLOADS = "Скачанное"


def shorten(text: str, limit: int = 34) -> str:
    """Cut to ``limit`` chars with an ellipsis — rows are one panel wide."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def pretty_date(raw: str) -> str:
    """ISO timestamp → ``2026-05-11 18:42``; anything else passes through."""
    raw = (raw or "").strip()
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def transcript_row_text(row: dict) -> tuple[str, str]:
    """``(title, date)`` for one transcription card.

    💻 marks desktop rows, 📱 rows sent through the Telegram bot (the
    owner view pools both).
    """
    glyph = "💻" if row.get("user_id") == "desktop" else "📱"
    label = shorten(row.get("label") or "(без названия)", 28)
    return f"{glyph} {label}", pretty_date(row.get("created_at") or "")


def reveal_in_explorer(path: Path) -> None:
    """Open Explorer with ``path`` selected (its folder elsewhere)."""
    if sys.platform == "win32":
        # One string, quoted path — explorer parses /select, itself.
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


class HistoryPanel(ctk.CTkFrame):
    """Collapsible left panel; ``get_settings`` feeds the owner scope."""

    LIMIT = 50
    WIDTH = 300

    def __init__(
        self,
        master,
        *,
        on_open: Callable[[dict, list[Segment]], None],
        get_settings: Callable[[], Settings],
    ):
        super().__init__(master, width=self.WIDTH)
        self._on_open = on_open
        self._get_settings = get_settings
        self._rows: list[ctk.CTkBaseClass] = []

        self._tabs = ctk.CTkSegmentedButton(
            self,
            values=[TAB_TRANSCRIPTS, TAB_DOWNLOADS],
            command=lambda _value: self.refresh(),
        )
        self._tabs.set(TAB_TRANSCRIPTS)
        self._tabs.pack(fill="x", padx=8, pady=(8, 4))

        self._list = ctk.CTkScrollableFrame(self, width=self.WIDTH - 30)
        self._list.pack(fill="both", expand=True, padx=4, pady=(4, 8))

    # ----- public API ---------------------------------------------------

    def refresh(self) -> None:
        for w in self._rows:
            try:
                w.destroy()
            except Exception:
                pass
        self._rows.clear()

        if self._tabs.get() == TAB_DOWNLOADS:
            self._fill_downloads()
        else:
            self._fill_transcripts()

    # ----- tabs ---------------------------------------------------------

    def _scope(self) -> tuple[str, ...]:
        return owner_scope(self._get_settings())

    def _fill_transcripts(self) -> None:
        try:
            rows = history.recent(self._scope(), limit=self.LIMIT)
        except Exception:
            logger.exception("Failed to load history")
            rows = []
        if not rows:
            self._show_empty("Здесь появятся расшифровки.")
            return
        for row in rows:
            self._rows.append(self._transcript_card(row))

    def _transcript_card(self, row: dict) -> ctk.CTkFrame:
        """Clickable card — a CTkButton would centre the two lines."""
        title, date = transcript_row_text(row)
        idle, hover = ("gray85", "gray22"), ("gray75", "gray30")

        card = ctk.CTkFrame(self._list, fg_color=idle, cursor="hand2")
        card.pack(fill="x", padx=2, pady=2)
        parts = [card, ctk.CTkLabel(card, text=title, anchor="w")]
        parts[-1].pack(fill="x", padx=8, pady=(6, 0 if date else 6))
        if date:
            parts.append(ctk.CTkLabel(
                card, text=date, anchor="w", text_color="gray55",
            ))
            parts[-1].pack(fill="x", padx=8, pady=(0, 6))

        for w in parts:
            w.bind("<Button-1>", lambda _e, r=row: self._open_row(r))
            w.bind("<Enter>", lambda _e: card.configure(fg_color=hover))
            w.bind("<Leave>", lambda _e: card.configure(fg_color=idle))
        return card

    def _fill_downloads(self) -> None:
        try:
            rows = history.recent_downloads("desktop", limit=self.LIMIT)
        except Exception:
            logger.exception("Failed to load downloads")
            rows = []
        if not rows:
            self._show_empty("Здесь появятся файлы, скачанные кнопкой «Скачать».")
            return
        for row in rows:
            self._rows.append(self._download_card(row))

    def _download_card(self, row: dict) -> ctk.CTkFrame:
        path = Path(row["file_path"])
        url = row["url"]
        exists = path.exists()

        card = ctk.CTkFrame(self._list, fg_color=("gray85", "gray22"))
        card.pack(fill="x", padx=2, pady=2)

        ctk.CTkLabel(
            card,
            text=shorten(path.name) + ("" if exists else "  (удалён)"),
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(fill="x", padx=8, pady=(6, 0))

        link = ctk.CTkLabel(
            card,
            text=shorten(url, 40),
            anchor="w",
            text_color=("#1f6aa5", "#6cb4ee"),
            cursor="hand2",
        )
        link.pack(fill="x", padx=8)
        link.bind("<Button-1>", lambda _e: webbrowser.open(url))

        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=8, pady=(2, 6))
        ctk.CTkLabel(
            bottom, text=pretty_date(row.get("created_at") or ""),
            anchor="w", text_color="gray55",
        ).pack(side="left")
        ctk.CTkButton(
            bottom, text="📁 Папка", width=72, height=24,
            state="normal" if exists else "disabled",
            command=lambda: self._reveal(path),
        ).pack(side="right")
        ctk.CTkButton(
            bottom, text="Ссылка", width=64, height=24,
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"),
            command=lambda: self._copy(url),
        ).pack(side="right", padx=(0, 4))
        return card

    # ----- actions ------------------------------------------------------

    def _show_empty(self, text: str) -> None:
        hint = ctk.CTkLabel(
            self._list, text=text, wraplength=self.WIDTH - 60,
            justify="center", text_color="gray55",
        )
        hint.pack(pady=30)
        self._rows.append(hint)

    def _open_row(self, row: dict) -> None:
        row_id = row.get("id")
        if row_id is None:
            return
        try:
            segments = history.get_segments(int(row_id), self._scope())
        except Exception:
            logger.exception("Failed to load segments for history id=%s", row_id)
            return
        if not segments:
            return
        try:
            self._on_open(row, segments)
        except Exception:
            logger.exception("on_open callback raised")

    def _reveal(self, path: Path) -> None:
        try:
            reveal_in_explorer(path)
        except Exception:
            logger.exception("could not reveal %s", path)

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
