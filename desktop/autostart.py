"""Windows autostart toggle via ``HKCU\\...\\Run`` registry key.

The `Run` value is a command line string; we point it at the frozen exe when
PyInstaller has packaged us, otherwise at ``pythonw.exe main.py`` so the
console window doesn't flash on every login.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_REG_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "VoiceTypeStudio"


def _open_run_key(write: bool):
    if sys.platform != "win32":
        raise RuntimeError("autostart is Windows-only")
    import winreg

    access = winreg.KEY_SET_VALUE if write else winreg.KEY_READ
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_PATH, 0, access)


def is_enabled() -> bool:
    """``True`` if the Run-key already points at us."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with _open_run_key(write=False) as key:
            winreg.QueryValueEx(key, _APP_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("Failed to read autostart registry key")
        return False


def _command_line() -> str:
    """The exact command Windows will run on login."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle — sys.executable is our .exe.
        return f'"{sys.executable}"'
    # Source mode — prefer pythonw.exe to suppress the console flash.
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.path.isfile(pyw):
        py = pyw
    main_py = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "main.py")
    )
    return f'"{py}" "{main_py}"'


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    with _open_run_key(write=True) as key:
        if enabled:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _command_line())
            logger.info("Autostart enabled: %s", _command_line())
        else:
            try:
                winreg.DeleteValue(key, _APP_NAME)
                logger.info("Autostart disabled")
            except FileNotFoundError:
                pass


def toggle() -> bool:
    """Flip the current state. Returns the new state."""
    new = not is_enabled()
    set_enabled(new)
    return new
