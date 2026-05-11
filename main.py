"""VoiceType Studio — entry point.

Wires together everything from ``core/`` and ``desktop/``:

* a single Tk root (customtkinter, DnD-aware via tkinterdnd2);
* an ``asyncio`` event loop running on a daemon thread (for aiogram and
  the ``Downloader`` / ``AudioConverter`` / ``Transcriber`` async APIs);
* a ``ThreadPoolExecutor(max_workers=1)`` shared by dictation, the
  transcriptor window and the bot — guarantees that GigaAM sees at most
  one in-flight call at a time (the model is not reentrant);
* tray, overlay, dictation, transcriptor window, settings window;
* shutdown that joins every helper thread within a 5 s budget.

Architectural invariants kept here:

* customtkinter on the main thread; aiogram in its own daemon thread.
* ``dp.start_polling(handle_signals=False)`` so aiogram doesn't try to
  register SIGINT from a non-main thread.
* GigaAM model lives in exactly one Python object across all UI surfaces
  — the ``Transcriber`` class-level singleton, used through the shared
  executor.
* Right-Ctrl dictation is **never** persisted — only the transcriptor
  window and the bot write to ``history.db``.
"""

from __future__ import annotations

# --- subprocess console-flash suppression (Windows) ---------------------
#
# torch / soundfile / ffmpeg-python sometimes spawn helper processes that
# would briefly pop a black console window in front of the user. We patch
# subprocess.Popen BEFORE any heavy import does its own spawn, hence the
# "before everything else" placement.
import sys

if sys.platform == "win32":
    import subprocess as _sp

    _Popen_orig = _sp.Popen.__init__

    def _Popen_no_window(self, *a, creationflags=0, **kw):
        _Popen_orig(self, *a, creationflags=creationflags | 0x08000000, **kw)

    _sp.Popen.__init__ = _Popen_no_window  # type: ignore[method-assign]


import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from bot.main import start_bot_polling, stop_bot_polling
from core import Downloader, Settings, Transcriber, config, settings_io
from desktop import single_instance
from desktop.dictation import DictationListener
from desktop.history_window import open_history_window
from desktop.overlay import Overlay
from desktop.settings_window import open_settings_window
from desktop.transcriptor_window import TranscriptorWindow
from desktop.tray import build_tray

logger = logging.getLogger(__name__)


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """``customtkinter`` root with ``tkinterdnd2`` capabilities mixed in.

    Without this mixin the transcriptor window's ``drop_target_register``
    silently no-ops — Toplevels inherit the DnD plumbing from the root.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # ``_require`` makes Tk load the tkdnd C extension shipped with
        # tkinterdnd2; from then on widgets can call drop_target_register.
        self.TkdndVersion = TkinterDnD._require(self)


class App:
    """Top-level lifecycle holder."""

    SHUTDOWN_TIMEOUT_S: float = 5.0

    def __init__(self) -> None:
        self.settings: Settings = settings_io.load()
        config.ensure_dirs()

        # --- Tk root ----------------------------------------------------
        self.root: CTkDnD = CTkDnD()
        self.root.withdraw()  # only the tray icon is meant to be visible
        self.root.title("VoiceType Studio")
        # Bundled .ico drives the taskbar / Alt+Tab / Проводник icon for
        # every Toplevel that doesn't override it. Path lookup handles
        # both frozen and source-mode runs.
        from core.assets import icon_ico_path

        ico = icon_ico_path()
        if ico:
            try:
                self.root.iconbitmap(default=ico)
            except Exception:
                logger.exception("Failed to apply app icon")

        # --- shared ASR executor ---------------------------------------
        self.asr_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="asr",
        )
        # Route every async call into Transcriber through this same executor
        # — guarantees serial GigaAM access from dictation + window + bot.
        Transcriber.set_executor(self.asr_executor)

        # Optional cookies.txt for yt-dlp (YouTube auth bypass).
        Downloader.set_cookies_file(self.settings.youtube_cookies_file)

        # --- asyncio loop on a daemon thread ---------------------------
        self.bot_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._bot_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="asyncio-loop",
        )
        self._bot_thread.start()

        # --- subcomponents ---------------------------------------------
        self.overlay = Overlay(self.root)
        self.dictation = DictationListener(
            transcribe_fn=self._transcribe_for_dictation,
            overlay=self.overlay,
        )
        self.dictation.start()

        self._transcriptor: Optional[TranscriptorWindow] = None
        self._settings_window = None  # CTkToplevel | None
        self._history_window = None  # CTkToplevel | None

        self.tray = build_tray(
            on_open_transcriptor=self._open_transcriptor_safe,
            on_open_settings=self._open_settings_safe,
            on_quit=self._quit_safe,
            on_open_data_folder=self._open_data_folder,
            on_clean_temp=self._clean_temp_files,
            on_open_history=self._open_history_safe,
        )

        # --- optional Telegram bot -------------------------------------
        if self.settings.bot_enabled and self.settings.bot_token.strip():
            self._start_bot()

        # When the (hidden) root receives a "destroy", shut down too.
        self.root.protocol("WM_DELETE_WINDOW", self._quit_safe)

        logger.info("App initialized")

    # ----- run / event loop --------------------------------------------

    def run(self) -> None:
        # Tray runs in its own thread; pystray.Icon.run() is blocking.
        threading.Thread(
            target=self.tray.run, daemon=True, name="tray",
        ).start()
        try:
            self.root.mainloop()
        finally:
            self.shutdown()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.bot_loop)
        try:
            self.bot_loop.run_forever()
        finally:
            try:
                self.bot_loop.close()
            except Exception:
                pass

    # ----- transcribe path used by dictation ---------------------------

    def _transcribe_for_dictation(self, audio, sample_rate: int) -> str:
        """Sync wrapper. Routes through ``asr_executor`` so the GigaAM
        model never sees overlapping calls from dictation + window."""
        future = self.asr_executor.submit(
            Transcriber.transcribe_array, audio, sample_rate,
        )
        return future.result(timeout=120)

    # ----- tray callbacks (run on the pystray thread) ------------------

    def _open_transcriptor_safe(self) -> None:
        self.root.after(0, self._open_transcriptor)

    def _open_settings_safe(self) -> None:
        self.root.after(0, self._open_settings)

    def _open_history_safe(self) -> None:
        self.root.after(0, self._open_history)

    def _quit_safe(self) -> None:
        self.root.after(0, self._quit)

    # ----- Tk-thread handlers ------------------------------------------

    def _open_transcriptor(self) -> None:
        existing = self._transcriptor
        if existing is None or not _winfo_alive(existing):
            self._transcriptor = TranscriptorWindow(
                self.root,
                bot_loop=self.bot_loop,
                asr_executor=self.asr_executor,
                settings=self.settings,
            )
        else:
            # Re-syncs settings in case they changed since last open.
            existing.settings = self.settings
        self._transcriptor.show()

    def _open_settings(self) -> None:
        if self._settings_window is not None and _winfo_alive(self._settings_window):
            self._settings_window.lift()
            self._settings_window.focus_force()
            return
        self._settings_window = open_settings_window(
            self.root,
            settings=self.settings,
            on_save=self._on_settings_saved,
            bot_loop=self.bot_loop,
        )

    def _open_history(self) -> None:
        if self._history_window is not None and _winfo_alive(self._history_window):
            try:
                self._history_window.lift()
                self._history_window.focus_force()
                # Refresh in case new rows landed since the window was opened.
                self._history_window.refresh()
            except Exception:
                pass
            return
        self._history_window = open_history_window(
            self.root,
            on_open=self._restore_history_row,
            settings=self.settings,
        )

    def _restore_history_row(self, row: dict, segments) -> None:
        """Push a stored transcription back into the Transcriptor window.

        Opens the Transcriptor if it isn't open yet — same code path as
        the tray's "Открыть транскриптор" entry.
        """
        self._open_transcriptor()
        if self._transcriptor is None:
            return
        try:
            self._transcriptor.restore_from_history(
                source_label=row.get("label", "(история)"),
                source=row.get("source", ""),
                segments=segments,
            )
        except Exception:
            logger.exception("Failed to restore history row %s", row.get("id"))

    def _on_settings_saved(self, new_settings: Settings) -> None:
        old = self.settings
        try:
            settings_io.save(new_settings)
        except Exception:
            logger.exception("Failed to write settings.json")
        self.settings = new_settings

        # Push the new settings into a live transcriptor window if open.
        if self._transcriptor is not None and _winfo_alive(self._transcriptor):
            self._transcriptor.settings = new_settings

        # Bot lifecycle deltas. Restart triggers:
        #   * token changed — Bot session needs the new value;
        #   * whitelist changed — WhitelistMiddleware caches a frozenset at
        #     construction time, so a running bot still rejects newly added
        #     IDs until we rebuild the dispatcher.
        # ``default_provider`` / ``api_keys`` flow through ``workflow_data``
        # and are picked up on the next handler call — no restart needed.
        was_on = bool(old.bot_enabled and old.bot_token.strip())
        is_on = bool(new_settings.bot_enabled and new_settings.bot_token.strip())
        token_changed = old.bot_token != new_settings.bot_token
        whitelist_changed = (
            sorted(old.whitelist_ids) != sorted(new_settings.whitelist_ids)
        )
        if is_on and not was_on:
            self._start_bot()
        elif was_on and not is_on:
            self._stop_bot()
        elif is_on and (token_changed or whitelist_changed):
            self._stop_bot()
            self._start_bot()

    def _open_data_folder(self) -> None:
        """Open Windows Explorer at ``data_dir`` so the user can see what's
        stored (history.db, silero/, tts/, tmp/)."""
        import subprocess

        path = config.data_dir
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            logger.exception("Failed to open data folder")

    def _clean_temp_files(self) -> None:
        """Wipe ``data/tmp/`` (downloads, intermediate WAVs, chunk dirs).

        Safe to call any time — running tasks write into per-uuid subpaths
        that we recreate on next download. ``tts/`` and ``history.db`` are
        deliberately spared.
        """
        import shutil

        tmp = config.temp_dir
        if not tmp.exists():
            logger.info("temp dir doesn't exist, nothing to clean")
            return
        bytes_freed = 0
        for entry in tmp.iterdir():
            try:
                if entry.is_dir():
                    bytes_freed += sum(
                        f.stat().st_size for f in entry.rglob("*") if f.is_file()
                    )
                    shutil.rmtree(entry, ignore_errors=True)
                elif entry.is_file():
                    bytes_freed += entry.stat().st_size
                    entry.unlink(missing_ok=True)
            except OSError:
                logger.debug("could not remove %s", entry, exc_info=True)
        logger.info("Freed %.1f MB from %s", bytes_freed / 1024 / 1024, tmp)

    def _quit(self) -> None:
        # Tearing the root down here triggers the ``finally`` in ``run``.
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ----- bot start/stop wrappers --------------------------------------

    def _start_bot(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                start_bot_polling(
                    settings=self.settings,
                    asr_executor=self.asr_executor,
                ),
                self.bot_loop,
            )
        except Exception:
            logger.exception("Failed to dispatch bot start")

    def _stop_bot(self) -> None:
        try:
            fut = asyncio.run_coroutine_threadsafe(
                stop_bot_polling(), self.bot_loop,
            )
            fut.result(timeout=self.SHUTDOWN_TIMEOUT_S)
        except Exception:
            logger.exception("Failed to stop bot cleanly")

    # ----- shutdown -----------------------------------------------------

    def shutdown(self) -> None:
        """Best-effort tear-down with hard timeouts.

        Order matters: stop external callers (bot, dictation, tray) before
        joining the asyncio thread; destroy the Tk root last so anything
        still calling ``root.after`` from a daemon thread doesn't blow up.
        """
        logger.info("Shutting down VoiceType Studio")

        # 1. stop accepting external triggers
        try:
            self._stop_bot()
        except Exception:
            pass
        try:
            self.dictation.stop()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass

        # 2. drain the asyncio loop
        try:
            self.bot_loop.call_soon_threadsafe(self.bot_loop.stop)
            self._bot_thread.join(timeout=self.SHUTDOWN_TIMEOUT_S)
        except Exception:
            pass

        # 3. cancel pending GigaAM calls
        try:
            self.asr_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

        # 4. destroy windows + root
        try:
            self.overlay.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

        logger.info("Shutdown complete")


# --- helpers ---------------------------------------------------------------


def _winfo_alive(widget) -> bool:
    """``winfo_exists`` returns truthy/falsy ints; wrap with try in case the
    widget is half-destroyed and the call itself raises."""
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not single_instance.acquire():
        single_instance.show_already_running_dialog()
        return
    App().run()


if __name__ == "__main__":
    main()
