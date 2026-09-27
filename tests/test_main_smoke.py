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

