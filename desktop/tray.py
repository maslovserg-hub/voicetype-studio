"""System-tray icon and right-click menu.

The menu stays short: Транскриптор, Настройки, Автозапуск (toggle),
О программе, Выход. Data-folder / temp-cleanup helpers moved into the
Settings window's «Обслуживание» section — the tray no longer exposes them
directly.

The tray runs in its own thread (``pystray.Icon.run_detached``); callbacks
fire on that thread, so anything that touches Tk widgets must be marshalled
back through the ``root.after`` queue. The caller passes plain functions —
this module doesn't know about the asyncio loop or queue.
"""

from __future__ import annotations

import logging
from typing import Callable

from . import autostart

logger = logging.getLogger(__name__)


def _make_icon_image():
    """Load the bundled robot icon for the tray. Falls back to a hand-
    drawn microphone if the assets aren't available (e.g., running from
    source with ``assets/`` missing).
    """
    from core.assets import load_icon_image

    bundled = load_icon_image(64)
    if bundled is not None:
        return bundled

    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill="#1e1e35")
    d.rounded_rectangle([22, 8, 42, 38], radius=10, fill="#e05a00")
    d.arc([14, 24, 50, 50], start=0, end=180, fill="#e05a00", width=3)
    d.line([32, 50, 32, 58], fill="#e05a00", width=3)
    d.line([24, 58, 40, 58], fill="#e05a00", width=3)
    return img


def build_tray(
    *,
    on_open_transcriptor: Callable[[], None],
    on_quit: Callable[[], None],
    on_open_settings: Callable[[], None] | None = None,
    on_about: Callable[[], None] | None = None,
):
    """Build a configured ``pystray.Icon``. Caller invokes ``.run_detached()``.

    All callbacks fire on the pystray thread — they must not touch Tk widgets
    directly. Wrap with ``root.after(0, ...)`` in main.py.
    """
    import pystray

    def _wrap(fn: Callable[[], None]):
        def _handler(icon, item):
            try:
                fn()
            except Exception:
                logger.exception("Tray menu callback failed")

        return _handler

    def _toggle_autostart(icon, item):
        try:
            autostart.toggle()
        except Exception:
            logger.exception("autostart.toggle failed")
        icon.update_menu()

    def _autostart_checked(item) -> bool:
        try:
            return autostart.is_enabled()
        except Exception:
            return False

    items = [
        pystray.MenuItem("VoiceType Studio", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Транскриптор", _wrap(on_open_transcriptor)),
    ]
    if on_open_settings is not None:
        items.append(pystray.MenuItem("Настройки", _wrap(on_open_settings)))
    items.extend(
        [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Запускать с Windows",
                _toggle_autostart,
                checked=_autostart_checked,
            ),
        ]
    )
    if on_about is not None:
        items.append(pystray.MenuItem("О программе", _wrap(on_about)))
    items.extend(
        [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", _wrap(on_quit)),
        ]
    )

    icon = pystray.Icon(
        "VoiceTypeStudio",
        _make_icon_image(),
        "VoiceType Studio",
        pystray.Menu(*items),
    )
    return icon
