"""Smoke tests for ``desktop.about_window``.

Needs a real Tk root (customtkinter is built on tkinter); these tests get
skipped if ``CTk()`` can't be constructed in the current environment — same
pattern as ``test_message_widget.py`` / ``test_settings_window.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def ctk_root():
    """Hidden CTk root, torn down after the test."""
    try:
        import customtkinter as ctk
    except Exception as e:  # pragma: no cover
        pytest.skip(f"customtkinter unavailable: {e}")
    try:
        root = ctk.CTk()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Cannot create Tk root in this env: {e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _all_widget_texts(widget) -> list[str]:
    out: list[str] = []
    try:
        out.append(widget.cget("text"))
    except Exception:
        pass
    for child in widget.winfo_children():
        out.extend(_all_widget_texts(child))
    return out


def test_about_window_module_imports() -> None:
    from desktop.about_window import AboutWindow, open_about_window

    assert AboutWindow is not None
    assert callable(open_about_window)


def test_about_window_shows_version_description_and_author(ctk_root) -> None:
    from core.version import __version__
    from desktop.about_window import AboutWindow

    win = AboutWindow(ctk_root)
    texts = _all_widget_texts(win)
    joined = "\n".join(texts)

    assert "VoiceType Studio" in texts
    assert any(__version__ in t for t in texts)
    assert "Автор: Сергей Маслов" in texts
    assert "Right Ctrl" in joined
    assert "Telegram-бот" in joined
    assert "Проверить обновления" in texts


def test_about_window_install_update_calls_on_start_update(ctk_root) -> None:
    """Mirrors the old settings_window behaviour: a successful
    ``updater.start_update()`` triggers ``on_start_update`` (main.py wires
    this to ``self._quit``)."""
    from unittest.mock import patch

    from desktop.about_window import AboutWindow

    calls = []
    win = AboutWindow(ctk_root, on_start_update=lambda: calls.append(True))

    with patch("desktop.about_window.updater.start_update", return_value=True):
        win._on_install_update()

    assert calls == [True]


def test_about_window_install_update_failure_shows_error(ctk_root) -> None:
    from unittest.mock import patch

    from desktop.about_window import AboutWindow

    calls = []
    win = AboutWindow(ctk_root, on_start_update=lambda: calls.append(True))

    with patch("desktop.about_window.updater.start_update", return_value=False):
        win._on_install_update()

    assert calls == []
    assert "не удалось" in win._update_status.cget("text").lower()
