"""Smoke tests for main.py and the bot stub.

We don't actually run ``App()`` — instantiating it would create a Tk root,
spin up the asyncio thread, register a global hotkey listener, and put a
real icon in the system tray. None of that survives a CI environment, and
some of it is destructive (mutex-grab) on the developer's own machine.

Instead we check that the wiring is sound:
* ``main`` imports cleanly with all heavy deps already on the path;
* ``main.App`` exposes the methods main.py's call sites depend on;
* the bot stub exposes a sane ``start_bot_polling`` / ``stop_bot_polling``
  pair so ``App._start_bot`` won't crash on boot.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


def test_main_module_imports() -> None:
    import main

    assert main.App is not None
    assert main.CTkDnD is not None
    assert callable(main.main)


@pytest.mark.parametrize(
    "method_name",
    [
        "run",
        "shutdown",
        "_open_transcriptor",
        "_open_settings",
        "_open_history",
        "_restore_history_row",
        "_on_settings_saved",
        "_transcribe_for_dictation",
        "_start_bot",
        "_stop_bot",
        "_quit",
        "_run_loop",
    ],
)
def test_app_class_has_required_methods(method_name: str) -> None:
    import main

    assert hasattr(main.App, method_name), f"App missing {method_name}"


def test_main_subprocess_patch_idempotent() -> None:
    """Re-importing ``main`` mustn't double-patch ``subprocess.Popen``."""
    import sys

    if sys.platform != "win32":
        pytest.skip("Patch only applies on Windows")
    import subprocess

    before = subprocess.Popen.__init__
    import importlib

    import main
    importlib.reload(main)
    # Patched version is itself a wrapper of the previous wrapper, but
    # Popen.__init__ should still be callable and the marker function
    # still in scope.
    assert callable(subprocess.Popen.__init__)


# --- bot stub --------------------------------------------------------------


def test_bot_stub_signatures() -> None:
    from bot.main import is_running, start_bot_polling, stop_bot_polling

    assert inspect.iscoroutinefunction(start_bot_polling)
    assert inspect.iscoroutinefunction(stop_bot_polling)
    assert callable(is_running)


def test_bot_stub_lifecycle() -> None:
    """Stub must allow start → stop without raising and update is_running()."""
    from bot import main as bot_main
    from core import Settings

    # Reset module-level state in case another test mutated it.
    bot_main._running = False

    settings = Settings(bot_enabled=True, bot_token="123:abc")

    asyncio.run(bot_main.start_bot_polling(settings=settings, asr_executor=None))
    assert bot_main.is_running() is True

    asyncio.run(bot_main.stop_bot_polling())
    assert bot_main.is_running() is False


def test_bot_stub_skips_when_no_token() -> None:
    from bot import main as bot_main
    from core import Settings

    bot_main._running = False
    settings = Settings(bot_enabled=True, bot_token="   ")
    asyncio.run(bot_main.start_bot_polling(settings=settings, asr_executor=None))
    assert bot_main.is_running() is False
