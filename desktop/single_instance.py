"""Windows-only single-instance guard via a named kernel mutex.

Acquire on startup; if another VoiceType Studio process already holds it the
kernel reports ``ERROR_ALREADY_EXISTS`` (183) and we exit quietly. The mutex
handle must outlive the process — keep a module-level reference, don't close
it.

On non-Windows platforms :func:`acquire` is a no-op; the project is
Windows-only but tests run cross-platform.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_ERROR_ALREADY_EXISTS = 183
_DEFAULT_NAME = "VoiceTypeStudio_SingleInstance"

# Module-level handle holder; the OS keeps the mutex alive as long as the
# handle isn't closed.
_handle: int | None = None


def acquire(name: str = _DEFAULT_NAME) -> bool:
    """Return ``True`` if this process is the sole instance, ``False`` otherwise.

    Idempotent — calling twice in the same process returns ``True`` both times.
    """
    global _handle
    if sys.platform != "win32":
        return True
    if _handle is not None:
        return True

    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.CreateMutexW(None, True, name)
    last_error = k32.GetLastError()
    if last_error == _ERROR_ALREADY_EXISTS:
        # Close the handle we got — another process owns the named mutex.
        if handle:
            k32.CloseHandle(handle)
        logger.info("VoiceType Studio is already running (mutex %r held).", name)
        return False

    _handle = handle
    return True


def show_already_running_dialog() -> None:
    """Convenience for ``main.py`` — pop up a tk messagebox then return."""
    if sys.platform != "win32":
        return
    import tkinter as tk
    import tkinter.messagebox as mb

    root = tk.Tk()
    root.withdraw()
    try:
        mb.showwarning(
            "VoiceType Studio",
            "VoiceType Studio уже запущен.",
        )
    finally:
        root.destroy()
