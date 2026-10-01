"""Settings panel — sections (AI / downloads / …) on a scrollable
CTk pane, slid in over the Transcriptor window.

Per FR-9 of the spec. Pure helpers (`display_to_provider_key`, …) live at module level so they can be unit-
tested without standing up a Tk root. The panel is opened by
:meth:`TranscriptorWindow.open_settings` and writes the result back
through an ``on_save`` callback.

The panel does not itself read or write ``settings.json`` — the caller
hands in the current :class:`Settings` and decides what to do with the
edited copy.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk

from core import Settings

from ._clipboard_menu import attach_clipboard_menu

logger = logging.getLogger(__name__)


# --- pure helpers (no UI) -----------------------------------------------

PROVIDER_DISPLAY: dict[str, str] = {
    "perplexity": "Perplexity",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}
PROVIDER_KEYS: tuple[str, ...] = tuple(PROVIDER_DISPLAY.keys())
PROVIDER_DISPLAY_VALUES: tuple[str, ...] = tuple(PROVIDER_DISPLAY.values())


def display_to_provider_key(display: str) -> str:
    """``"OpenAI"`` → ``"openai"``. Case-insensitive; unknown values pass
    through unchanged so callers don't silently lose user input."""
    d = (display or "").strip().lower()
    for key, label in PROVIDER_DISPLAY.items():
        if d == key or d == label.lower():
            return key
    return d


def provider_key_to_display(key: str) -> str:
    return PROVIDER_DISPLAY.get(key, key)


# --- panel ---------------------------------------------------------


class SettingsPanel(ctk.CTkFrame):
    """Editable form for one :class:`Settings` instance."""

    WIDTH = 560

    def __init__(
        self,
        master,
        *,
        settings: Settings,
        on_save: Callable[[Settings], None],
        on_open_data_folder: Optional[Callable[[], None]] = None,
        on_clean_temp: Optional[Callable[[], None]] = None,
        on_close: Callable[[], None] = lambda: None,
    ):
        super().__init__(master, width=self.WIDTH, border_width=1)
        self.pack_propagate(False)  # keep WIDTH, don't shrink to content
        self._on_close = on_close

        self._initial = settings
        self._on_save = on_save
        self._on_open_data_folder = on_open_data_folder
        self._on_clean_temp = on_clean_temp

        # Scrollable body so smaller screens still see Save/Cancel.
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        self._build_ai_section(body, settings)
        self._build_download_section(body, settings)
        self._build_maintenance_section(body)

        # Footer (sticky).
        footer = ctk.CTkFrame(self)
        footer.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(
            footer, text="Отмена", width=100, command=self._cancel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer, text="Сохранить", width=120, command=self._save,
        ).pack(side="right")
        self._error_label = ctk.CTkLabel(
            footer, text="", text_color="#ff6b6b", anchor="w",
        )
        self._error_label.pack(side="left", fill="x", expand=True)

    # ----- builders -----------------------------------------------------

    def _build_ai_section(self, parent, s: Settings) -> None:
        _section_header(parent, "AI / LLM")

        _row_label(parent, "Провайдер по умолчанию")
        self._provider_var = ctk.StringVar(
            value=provider_key_to_display(s.default_provider)
        )
        ctk.CTkOptionMenu(
            parent,
            values=list(PROVIDER_DISPLAY_VALUES),
            variable=self._provider_var,
        ).pack(fill="x", pady=(0, 8))

        _row_label(parent, "Запасные провайдеры (Favorites)")
        favorites_set = set(s.favorites or [])
        self._fav_vars: dict[str, ctk.BooleanVar] = {}
        favs_frame = ctk.CTkFrame(parent, fg_color="transparent")
        favs_frame.pack(fill="x", pady=(0, 8))
        for key in PROVIDER_KEYS:
            var = ctk.BooleanVar(value=(key in favorites_set))
            self._fav_vars[key] = var
            ctk.CTkCheckBox(
                favs_frame, text=PROVIDER_DISPLAY[key], variable=var,
            ).pack(side="left", padx=(0, 12))

        self._key_entries: dict[str, ctk.CTkEntry] = {}
        for key in PROVIDER_KEYS:
            _row_label(parent, f"{PROVIDER_DISPLAY[key]} API key")
            entry = ctk.CTkEntry(parent, show="*")
            entry.insert(0, s.api_key_for(key))
            entry.pack(fill="x", pady=(0, 8))
            attach_clipboard_menu(entry)
            self._key_entries[key] = entry

        _row_label(parent, "Yandex SpeechKit API key (язык «Другой»)")
        self._speechkit_entry = ctk.CTkEntry(parent, show="*")
        self._speechkit_entry.insert(0, s.speechkit_api_key or "")
        self._speechkit_entry.pack(fill="x", pady=(0, 8))
        attach_clipboard_menu(self._speechkit_entry)

    def _build_download_section(self, parent, s: Settings) -> None:
        _section_header(parent, "Скачивание")
        _row_label(parent, "Папка для кнопки «Скачать» (пусто — «Загрузки»)")

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))

        self._download_dir_entry = ctk.CTkEntry(
            row, placeholder_text="Загрузки (по умолчанию)",
        )
        self._download_dir_entry.insert(0, s.download_dir or "")
        self._download_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        attach_clipboard_menu(self._download_dir_entry)

        ctk.CTkButton(
            row, text="Выбрать…", width=90,
            command=self._on_pick_download_dir,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            row, text="По умолчанию", width=110,
            command=lambda: self._download_dir_entry.delete(0, "end"),
        ).pack(side="left", padx=2)

    def _on_pick_download_dir(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(
            title="Куда сохранять скачанные файлы",
            initialdir=self._download_dir_entry.get().strip() or None,
            parent=self,
        )
        if path:
            self._download_dir_entry.delete(0, "end")
            self._download_dir_entry.insert(0, path)

    def _build_maintenance_section(self, parent) -> None:
        _section_header(parent, "Обслуживание")

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            row, text="Открыть папку с данными", width=200,
            command=self._on_open_data_folder_clicked,
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Очистить временные файлы", width=200,
            command=self._on_clean_temp_clicked,
        ).pack(side="left", padx=(8, 0))

        self._maintenance_status = ctk.CTkLabel(parent, text="", anchor="w")
        self._maintenance_status.pack(fill="x", pady=(0, 4))

    def _on_open_data_folder_clicked(self) -> None:
        if self._on_open_data_folder is None:
            return
        try:
            self._on_open_data_folder()
        except Exception as exc:
            logger.exception("on_open_data_folder callback raised")
            self._maintenance_status.configure(
                text=f"Не удалось открыть папку: {exc}", text_color="#ff6b6b",
            )

    def _on_clean_temp_clicked(self) -> None:
        if self._on_clean_temp is None:
            return
        try:
            self._on_clean_temp()
        except Exception as exc:
            logger.exception("on_clean_temp callback raised")
            self._maintenance_status.configure(
                text=f"Не удалось очистить: {exc}", text_color="#ff6b6b",
            )
            return
        self._maintenance_status.configure(
            text="Временные файлы удалены.", text_color="#3ea55a",
        )

    # ----- callbacks ----------------------------------------------------

    def _save(self) -> None:
        new = self._collect()
        self._error_label.configure(text="")
        try:
            self._on_save(new)
        except Exception as exc:
            logger.exception("on_save callback raised")
            self._error_label.configure(text=f"Не удалось сохранить: {exc}")
            return
        self._on_close()

    def _cancel(self) -> None:
        self._on_close()

    # ----- form ↔ Settings mapping --------------------------------------

    def _collect(self) -> Settings:
        default_provider = display_to_provider_key(self._provider_var.get())
        favorites = [k for k, v in self._fav_vars.items() if v.get()]
        api_keys = {
            k: e.get().strip() for k, e in self._key_entries.items()
            if e.get().strip()
        }
        return Settings(
            default_provider=default_provider,
            favorites=favorites,
            api_keys=api_keys,
            download_dir=self._download_dir_entry.get().strip(),
            speechkit_api_key=self._speechkit_entry.get().strip(),
        )


# --- private helpers ----------------------------------------------------


def _section_header(parent, text: str) -> None:
    ctk.CTkLabel(
        parent,
        text=text,
        anchor="w",
        font=ctk.CTkFont(size=14, weight="bold"),
    ).pack(fill="x", pady=(12, 4))
    sep = ctk.CTkFrame(parent, height=1, fg_color="#3a3a4c")
    sep.pack(fill="x", pady=(0, 8))


def _row_label(parent, text: str) -> None:
    ctk.CTkLabel(parent, text=text, anchor="w").pack(fill="x")
