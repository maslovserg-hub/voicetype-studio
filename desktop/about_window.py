"""«О программе» window — version, feature summary, author, updates.

The update-check / install flow used to live in the Settings window's
«Обновление» section; it moved here verbatim (same ``core.updater`` calls,
same future-polling through ``bot_loop`` when available) since checking for
updates isn't something you do per-session the way you tweak settings.

Invoked from ``main.py`` via :func:`open_about_window`, mirroring
``settings_window.open_settings_window``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import customtkinter as ctk

from core import updater
from core.version import __version__

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Распознавание и обработка русской речи:\n"
    " • диктовка в любое окно по Right Ctrl\n"
    " • транскрибация файлов и ссылок (YouTube и др.)\n"
    " • краткое изложение и обработка текста через LLM\n"
    " • озвучка текста\n"
    " • Telegram-бот (по желанию)"
)

AUTHOR = "Сергей Маслов"


class AboutWindow(ctk.CTkToplevel):
    """Read-only info panel + «Проверить обновления» button."""

    def __init__(
        self,
        master,
        *,
        bot_loop: Optional[asyncio.AbstractEventLoop] = None,
        on_start_update: Optional[Callable[[], None]] = None,
    ):
        super().__init__(master)
        self.title("VoiceType Studio — О программе")
        self.geometry("480x360")
        self.minsize(420, 320)
        self.resizable(False, False)
        self.transient(master)

        self._bot_loop = bot_loop
        self._on_start_update = on_start_update

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=16)

        header = ctk.CTkFrame(body, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text="VoiceType Studio",
            font=ctk.CTkFont(size=16, weight="bold"), anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=f"версия {__version__}", anchor="e",
            text_color="#aaaaaa",
        ).pack(side="right")

        ctk.CTkLabel(
            body, text=DESCRIPTION, anchor="w", justify="left",
            wraplength=440,
        ).pack(fill="x", pady=(12, 0))

        ctk.CTkLabel(
            body, text=f"Автор: {AUTHOR}", anchor="w",
        ).pack(fill="x", pady=(16, 0))

        update_row = ctk.CTkFrame(body, fg_color="transparent")
        update_row.pack(fill="x", pady=(16, 0))
        self._check_update_btn = ctk.CTkButton(
            update_row, text="Проверить обновления", width=180,
            command=self._on_check_updates,
        )
        self._check_update_btn.pack(side="left")
        self._update_status = ctk.CTkLabel(update_row, text="", anchor="w")
        self._update_status.pack(side="left", padx=(8, 0), fill="x", expand=True)

        self._install_update_btn = ctk.CTkButton(
            body, text="", command=self._on_install_update,
        )  # packed only once a newer version is found

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(
            footer, text="Закрыть", width=100, command=self.destroy,
        ).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ----- update check/install (moved from settings_window) ------------

    def _on_check_updates(self) -> None:
        self._check_update_btn.configure(state="disabled")
        self._update_status.configure(text="Проверяю…", text_color="#aaaaaa")

        async def _check() -> tuple[bool, str]:
            try:
                latest = await updater.latest_version()
            except Exception as exc:
                return False, f"Не удалось проверить: {exc}"
            if updater.is_newer(latest):
                return True, latest
            return False, "У вас последняя версия."

        if self._bot_loop is not None:
            fut = asyncio.run_coroutine_threadsafe(_check(), self._bot_loop)
            self.after(100, lambda: self._poll_update_future(fut))
        else:
            import threading

            def _runner() -> None:
                found, payload = asyncio.run(_check())
                self.after(0, lambda: self._show_update_result(found, payload))

            threading.Thread(target=_runner, daemon=True).start()

    def _poll_update_future(self, fut) -> None:
        if not fut.done():
            self.after(100, lambda: self._poll_update_future(fut))
            return
        try:
            found, payload = fut.result()
        except Exception as e:
            found, payload = False, f"Ошибка: {e}"
        self._show_update_result(found, payload)

    def _show_update_result(self, found: bool, payload: str) -> None:
        self._check_update_btn.configure(state="normal")
        if not found:
            self._update_status.configure(text=payload, text_color="#aaaaaa")
            return
        self._update_status.configure(
            text=(
                f"Доступна версия {payload}. Программа закроется, скачает "
                "около 220 МБ и запустится снова."
            ),
            text_color="#3ea55a",
        )
        self._install_update_btn.configure(text=f"Обновить до {payload}")
        self._install_update_btn.pack(fill="x", padx=16, pady=(0, 8))

    def _on_install_update(self) -> None:
        if not updater.start_update():
            self._update_status.configure(
                text=(
                    "Не удалось запустить обновление — в сборке нет скрипта "
                    "(запущено из исходников?)."
                ),
                text_color="#ff6b6b",
            )
            return
        if self._on_start_update is not None:
            self._on_start_update()


# --- standalone-ish entry point used by main.py --------------------------


def open_about_window(
    master,
    *,
    bot_loop: Optional[asyncio.AbstractEventLoop] = None,
    on_start_update: Optional[Callable[[], None]] = None,
) -> AboutWindow:
    """Build, show, and return the window. Caller keeps the reference so it
    isn't garbage-collected before the user closes it."""
    win = AboutWindow(master, bot_loop=bot_loop, on_start_update=on_start_update)
    win.lift()
    win.focus_force()
    return win
