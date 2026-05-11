"""Settings window — three sections (AI / TTS / Telegram) on a scrollable
CTk pane.

Per FR-9 of the spec. Pure helpers (`parse_whitelist_ids`,
`display_to_provider_key`, …) live at module level so they can be unit-
tested without standing up a Tk root. The window itself is invoked from
``main.py`` via :func:`open_settings_window`, which builds, blocks (modal-
ish), and writes the result back through an ``on_save`` callback.

The window does not itself read or write ``settings.json`` — the caller
hands in the current :class:`Settings` and decides what to do with the
edited copy.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

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

TTS_SPEAKERS: tuple[str, ...] = ("aidar", "baya", "kseniya", "xenia", "eugene")


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


def parse_whitelist_ids(raw: str) -> list[int]:
    """Comma- or whitespace-separated → ``list[int]``. Non-numeric tokens
    are dropped silently — better UX than refusing to save the form."""
    out: list[int] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            logger.debug("Dropping non-numeric whitelist token: %r", chunk)
    return out


def format_whitelist_ids(ids: list[int]) -> str:
    return ", ".join(str(i) for i in ids or [])


# --- Telegram token validation ------------------------------------------


async def validate_telegram_token(token: str) -> tuple[bool, str]:
    """Hit Telegram's ``getMe`` and report ``(ok, human_message)``.

    Used by the «Проверить токен» button. Lives at module level so it can
    be exercised by a live test without the GUI.
    """
    import aiohttp

    cleaned = (token or "").strip()
    if not cleaned:
        return False, "Пустой токен"

    url = f"https://api.telegram.org/bot{cleaned}/getMe"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                status = resp.status
                try:
                    data = await resp.json()
                except aiohttp.ContentTypeError:
                    data = {}
    except Exception as exc:
        return False, f"Сетевая ошибка: {exc}"

    if status == 200 and data.get("ok"):
        username = data.get("result", {}).get("username", "?")
        return True, f"OK — @{username}"
    description = data.get("description") or f"HTTP {status}"
    return False, str(description)


# --- main window ---------------------------------------------------------


class SettingsWindow(ctk.CTkToplevel):
    """Editable form for one :class:`Settings` instance."""

    def __init__(
        self,
        master,
        *,
        settings: Settings,
        on_save: Callable[[Settings], None],
        token_validator: Optional[
            Callable[[str], Awaitable[tuple[bool, str]]]
        ] = None,
        bot_loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        super().__init__(master)
        self.title("VoiceType Studio — Настройки")
        self.geometry("620x700")
        self.minsize(520, 600)
        self.transient(master)

        self._initial = settings
        self._on_save = on_save
        self._token_validator = token_validator or validate_telegram_token
        self._bot_loop = bot_loop

        # Scrollable body so smaller screens still see Save/Cancel.
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        self._build_ai_section(body, settings)
        self._build_tts_section(body, settings)
        self._build_youtube_section(body, settings)
        self._build_telegram_section(body, settings)

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

        self.protocol("WM_DELETE_WINDOW", self._cancel)

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

    def _build_tts_section(self, parent, s: Settings) -> None:
        _section_header(parent, "Озвучка (silero TTS)")
        _row_label(parent, "Голос")
        self._tts_speaker_var = ctk.StringVar(
            value=s.tts_speaker if s.tts_speaker in TTS_SPEAKERS else TTS_SPEAKERS[-1]
        )
        ctk.CTkOptionMenu(
            parent, values=list(TTS_SPEAKERS), variable=self._tts_speaker_var,
        ).pack(fill="x", pady=(0, 8))

    def _build_youtube_section(self, parent, s: Settings) -> None:
        _section_header(parent, "YouTube cookies (для приватных / возрастных)")

        ctk.CTkLabel(
            parent,
            text=(
                "Опционально. Если YouTube спрашивает «не бот ли вы», экспортируй "
                "cookies из браузера расширением «Get cookies.txt LOCALLY» и укажи "
                "файл здесь."
            ),
            anchor="w",
            wraplength=560,
            justify="left",
            text_color="#888888",
        ).pack(fill="x", pady=(0, 6))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))

        self._cookies_entry = ctk.CTkEntry(
            row, placeholder_text="Путь к cookies.txt",
        )
        self._cookies_entry.insert(0, s.youtube_cookies_file or "")
        self._cookies_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        attach_clipboard_menu(self._cookies_entry)

        ctk.CTkButton(
            row, text="Обзор…", width=90,
            command=self._on_pick_cookies_file,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            row, text="Очистить", width=90,
            command=lambda: (
                self._cookies_entry.delete(0, "end")
            ),
        ).pack(side="left", padx=2)

    def _on_pick_cookies_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Выберите cookies.txt",
            filetypes=[
                ("Cookies file", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._cookies_entry.delete(0, "end")
            self._cookies_entry.insert(0, path)

    def _build_telegram_section(self, parent, s: Settings) -> None:
        _section_header(parent, "Telegram-бот")

        self._bot_enabled_var = ctk.BooleanVar(value=s.bot_enabled)
        ctk.CTkSwitch(
            parent, text="Включить Telegram-бот", variable=self._bot_enabled_var,
            command=self._on_bot_enabled_changed,
        ).pack(anchor="w", pady=(0, 8))

        self._tg_block = ctk.CTkFrame(parent, fg_color="transparent")
        self._tg_block.pack(fill="x")

        _row_label(self._tg_block, "Bot Token")
        self._token_entry = ctk.CTkEntry(self._tg_block, show="*")
        self._token_entry.insert(0, s.bot_token or "")
        self._token_entry.pack(fill="x", pady=(0, 4))
        attach_clipboard_menu(self._token_entry)

        token_actions = ctk.CTkFrame(self._tg_block, fg_color="transparent")
        token_actions.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            token_actions, text="Проверить токен",
            width=160, command=self._on_test_token,
        ).pack(side="left")
        self._token_status = ctk.CTkLabel(token_actions, text="", anchor="w")
        self._token_status.pack(side="left", padx=(8, 0), fill="x", expand=True)

        _row_label(
            self._tg_block,
            "Whitelist Telegram IDs (через запятую) — кому разрешён доступ",
        )
        self._whitelist_entry = ctk.CTkEntry(self._tg_block)
        self._whitelist_entry.insert(0, format_whitelist_ids(s.whitelist_ids))
        self._whitelist_entry.pack(fill="x", pady=(0, 8))
        attach_clipboard_menu(self._whitelist_entry)

        # Initial visibility.
        self._on_bot_enabled_changed()

    # ----- callbacks ----------------------------------------------------

    def _on_bot_enabled_changed(self) -> None:
        # Even when disabled, leave the inputs visible (the user may want to
        # type before flipping the switch). Just dim the help text to make it
        # obvious nothing's running.
        if self._bot_enabled_var.get():
            self._tg_block.configure(fg_color="transparent")
        else:
            self._tg_block.configure(fg_color="transparent")

    def _on_test_token(self) -> None:
        token = self._token_entry.get().strip()
        self._token_status.configure(text="Проверяю…", text_color="#aaaaaa")

        # If we have an asyncio loop, dispatch there; else use a one-shot
        # ``asyncio.run`` on a worker thread so the GUI keeps responding.
        if self._bot_loop is not None:
            fut = asyncio.run_coroutine_threadsafe(
                self._token_validator(token), self._bot_loop,
            )
            self.after(100, lambda: self._poll_token_future(fut))
        else:
            import threading

            def _runner():
                ok, msg = asyncio.run(self._token_validator(token))
                self.after(0, lambda: self._show_token_status(ok, msg))

            threading.Thread(target=_runner, daemon=True).start()

    def _poll_token_future(self, fut) -> None:
        if not fut.done():
            self.after(100, lambda: self._poll_token_future(fut))
            return
        try:
            ok, msg = fut.result()
        except Exception as e:
            ok, msg = False, f"Ошибка: {e}"
        self._show_token_status(ok, msg)

    def _show_token_status(self, ok: bool, message: str) -> None:
        color = "#3ea55a" if ok else "#ff6b6b"
        self._token_status.configure(text=message, text_color=color)

    def _save(self) -> None:
        new = self._collect()
        # Validation: bot enabled with empty token is a configuration mistake.
        if new.bot_enabled and not new.bot_token.strip():
            self._error_label.configure(
                text="Включён бот, но Bot Token пустой."
            )
            return
        self._error_label.configure(text="")
        try:
            self._on_save(new)
        except Exception as exc:
            logger.exception("on_save callback raised")
            self._error_label.configure(text=f"Не удалось сохранить: {exc}")
            return
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()

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
            tts_speaker=self._tts_speaker_var.get(),
            bot_enabled=bool(self._bot_enabled_var.get()),
            bot_token=self._token_entry.get().strip(),
            whitelist_ids=parse_whitelist_ids(self._whitelist_entry.get()),
            youtube_cookies_file=self._cookies_entry.get().strip(),
        )


# --- standalone-ish entry point used by main.py --------------------------


def open_settings_window(
    master,
    *,
    settings: Settings,
    on_save: Callable[[Settings], None],
    bot_loop: Optional[asyncio.AbstractEventLoop] = None,
) -> SettingsWindow:
    """Build, show, and return the window. Caller keeps the reference so it
    isn't garbage-collected before the user closes it."""
    win = SettingsWindow(
        master, settings=settings, on_save=on_save, bot_loop=bot_loop,
    )
    win.lift()
    win.focus_force()
    return win


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
