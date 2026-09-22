"""Update check + handover to the update script.

The app never unpacks anything itself: it can't overwrite its own exe
while running. Both the «Обновить» button and the standalone
``VoiceTypeStudio-Update.bat`` run the *same* script, which stops the
app, downloads the latest release zip and unpacks it over the install
folder. The button just starts that script and quits.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .http import client_session
from .version import __version__

logger = logging.getLogger(__name__)

LATEST_API = (
    "https://api.github.com/repos/maslovserg-hub/voicetype-studio/releases/latest"
)
SCRIPT_NAME = "VoiceTypeStudio-Update.bat"


def parse_version(raw: str) -> tuple[int, ...]:
    """``"v1.0.3"`` → ``(1, 0, 3)``. Non-numeric parts are dropped, so a
    tag like ``v1.0.3-beta`` still compares as ``(1, 0, 3)``."""
    cleaned = (raw or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


async def latest_version() -> str:
    """Tag of the newest GitHub release, e.g. ``"1.0.4"``."""
    async with client_session() as session:
        async with session.get(
            LATEST_API, headers={"Accept": "application/vnd.github+json"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return str(data.get("tag_name") or "").lstrip("vV")


def script_path() -> Optional[Path]:
    """The bundled update script, or ``None`` when running from source."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
    candidate = (base / "tools" / SCRIPT_NAME) if base else Path("tools") / SCRIPT_NAME
    return candidate if candidate.is_file() else None


def start_update() -> bool:
    """Copy the script out of the install folder and run it.

    The copy matters: cmd reads a .bat line by line while it runs, and
    the script overwrites the install folder it lives in. ``os.startfile``
    (not ``Popen``) because main.py forces CREATE_NO_WINDOW on every
    Popen — the user should see the progress window.
    """
    src = script_path()
    if src is None:
        logger.warning("update script not found — running from source?")
        return False
    try:
        dst = Path(tempfile.gettempdir()) / SCRIPT_NAME
        shutil.copyfile(src, dst)
        os.startfile(str(dst))  # type: ignore[attr-defined]
        return True
    except Exception:
        logger.exception("could not start the update script")
        return False
