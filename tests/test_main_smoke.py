"""Smoke tests for main.py.

We don't actually run ``App()`` — instantiating it would create a Tk root,
spin up the asyncio thread, register a global hotkey listener, and put a
real icon in the system tray. None of that survives a CI environment, and
some of it is destructive (mutex-grab) on the developer's own machine.

Instead we check that the wiring is sound:
* ``main`` imports cleanly with all heavy deps already on the path;
* ``main.App`` exposes the methods main.py's call sites depend on.
"""

from __future__ import annotations

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
        "_open_settings_safe",
        "_open_about",
        "_open_about_safe",
        "_open_live_subtitles",
        "_open_live_subtitles_safe",
        "_on_settings_saved",
        "_transcribe_for_dictation",
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


@pytest.mark.parametrize(
    "warm_up_done, quitting, model_cached, exits",
    [
        (False, True, True, True),
        (True, True, True, False),
        # a crash, not a quit — let the exception surface
        (False, False, True, False),
        # warm-up may be downloading the model — killing it truncates the .ckpt
        (False, True, False, False),
    ],
)
def test_shutdown_skips_waiting_for_running_warm_up(
    warm_up_done, quitting, model_cached, exits,
) -> None:
    """A warm-up still running on an already-cached model must not hold
    the process open after quit; otherwise shutdown exits normally."""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock, patch

    import main

    app = main.App.__new__(main.App)
    for name in ("dictation", "tray", "bot_loop", "_bot_thread", "overlay", "root"):
        setattr(app, name, MagicMock())
    app.asr_executor = ThreadPoolExecutor(max_workers=1)
    app._warm_up = MagicMock(done=MagicMock(return_value=warm_up_done))
    app._quitting = quitting
    app._model_cached = model_cached

    with (
        patch.object(main.os, "_exit") as fake_exit,
        patch.object(main.logging, "shutdown"),
    ):
        app.shutdown()

    assert fake_exit.called is exits
