"""Locate files bundled under ``assets/`` at runtime.

PyInstaller copies whatever ``VoiceTypeStudio.spec`` declares in ``datas``
into ``sys._MEIPASS/assets/`` next to the exe. In a source-mode dev run
the same files live in ``<repo>/assets/``. This module hides the fork.

Used by:
* ``desktop/tray.py`` — tray icon PNG;
* ``main.py`` — Tk root ``iconbitmap``;
* ``bot/handlers/start.py`` — ``/start`` welcome photo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _assets_root() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "assets"


def asset_path(name: str) -> str | None:
    """Absolute path to ``assets/<name>`` if it exists, ``None`` otherwise."""
    p = _assets_root() / name
    return str(p) if p.is_file() else None


def icon_ico_path() -> str | None:
    return asset_path("icon.ico")


def icon_png_path(size: int = 64) -> str | None:
    return asset_path(f"icon_{size}.png")


def bot_welcome_photo_path() -> str | None:
    return asset_path("bot.png")


def load_icon_image(size: int = 64):
    """PIL.Image of the icon at the requested size, or ``None`` if assets
    aren't bundled. Falls back to the largest available file and resizes."""
    try:
        from PIL import Image
    except ImportError:
        return None

    for candidate in (size, 256, 64):
        p = icon_png_path(candidate)
        if p:
            img = Image.open(p)
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            return img
    return None
