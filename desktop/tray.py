"""System-tray icon and right-click menu.

Per spec FR-2 the menu has four real entries: Транскриптор, Настройки,
Автозапуск (toggle), Выход. ``About`` is a separator-style label only.

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
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Dark circle background (matches Voice Type's existing icon).
    d.ellipse([2, 2, 62, 62], fill="#1e1e35")
    # Microphone body — accent orange.
    d.rounded_rectangle([22, 8, 42, 38], radius=10, fill="#e05a00")
    # Stand: arc + post + base.
    d.arc([14, 24, 50, 50], start=0, end=180, fill="#e05a00", width=3)
    d.line([32, 50, 32, 58], fill="#e05a00", width=3)
    d.line([24, 58, 40, 58], fill="#e05a00", width=3)
    return img


def build_tray(
    *,
    on_open_transcriptor: Callable[[], None],
    on_open_settings: Callable[[], None],
    on_quit: Callable[[], None],
    on_about: Callable[[], None] | None = None,
    on_open_data_folder: Callable[[], None] | None = None,
    on_clean_temp: Callable[[], None] | None = None,
    on_open_history: Callable[[], None] | None = None,
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
        pystray.MenuItem("Открыть транскриптор", _wrap(on_open_transcriptor)),
    ]
    if on_open_history is not None:
        items.append(pystray.MenuItem("История…", _wrap(on_open_history)))
    items.extend(
        [
            pystray.MenuItem("Настройки…", _wrap(on_open_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Запускать с Windows",
                _toggle_autostart,
                checked=_autostart_checked,
            ),
        ]
    )
    if on_open_data_folder is not None:
        items.append(pystray.MenuItem(
            "Папка с данными", _wrap(on_open_data_folder),
        ))
    if on_clean_temp is not None:
        items.append(pystray.MenuItem(
            "Очистить временные файлы", _wrap(on_clean_temp),
        ))
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
