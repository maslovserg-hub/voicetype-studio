"""Tests for the right-click context menu helper.

We don't open a real Tk root in CI — the test creates a hidden
``tk.Tk()``, attaches a menu, and inspects its items.
"""

from __future__ import annotations

import sys
import tkinter as tk

import pytest


@pytest.fixture(scope="module")
def root():
    """Hidden Tk root — module-scoped because tkinter doesn't reliably
    handle ``Tk()`` create/destroy/create in a single process."""
    try:
        r = tk.Tk()
    except tk.TclError as e:  # headless CI without a display
        pytest.skip(f"no display available: {e}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


def _menu_labels(menu: tk.Menu) -> list[str]:
    out = []
    for i in range(menu.index("end") + 1):
        try:
            out.append(menu.entrycget(i, "label"))
        except tk.TclError:
            out.append("---")  # separator
    return out


def test_full_menu_has_cut_copy_paste_select_all(root) -> None:
    from desktop._clipboard_menu import attach_clipboard_menu

    entry = tk.Entry(root)
    menu = attach_clipboard_menu(entry)
    labels = _menu_labels(menu)
    assert "Вырезать" in labels
    assert "Копировать" in labels
    assert "Вставить" in labels
    assert "Выделить всё" in labels
    assert "---" in labels  # separator before Select All


def test_paste_disabled_for_readonly(root) -> None:
    from desktop._clipboard_menu import attach_clipboard_menu

    textbox = tk.Text(root)
    menu = attach_clipboard_menu(textbox, paste=False, cut=False)
    labels = _menu_labels(menu)
    assert "Вставить" not in labels
    assert "Вырезать" not in labels
    assert "Копировать" in labels


def test_button3_binding_attached(root) -> None:
    from desktop._clipboard_menu import attach_clipboard_menu

    entry = tk.Entry(root)
    attach_clipboard_menu(entry)
    # ``bind`` returns the binding string when queried with no callback.
    assert entry.bind("<Button-3>"), "right-click handler missing"


def test_inner_text_widget_unwraps_ctk(root) -> None:
    """CTkEntry wraps a tk.Entry inside; menu actions must target the inner
    widget or virtual events fire on the wrong thing and Paste silently no-ops."""
    from desktop._clipboard_menu import _inner_text_widget

    # Fake a CTkEntry-shaped object: anything with ``._entry`` attr.
    class FakeCTkEntry:
        def __init__(self, inner):
            self._entry = inner

    inner = tk.Entry(root)
    fake = FakeCTkEntry(inner)
    assert _inner_text_widget(fake) is inner

    # CTkTextbox shape — ``._textbox``.
    class FakeCTkTextbox:
        def __init__(self, inner):
            self._textbox = inner

    inner_text = tk.Text(root)
    fake_tb = FakeCTkTextbox(inner_text)
    assert _inner_text_widget(fake_tb) is inner_text


def test_inner_widget_passthrough_for_vanilla_tk(root) -> None:
    """A plain tk.Entry should be returned as-is."""
    from desktop._clipboard_menu import _inner_text_widget

    entry = tk.Entry(root)
    assert _inner_text_widget(entry) is entry


def test_select_all_on_entry(root) -> None:
    """The select-all helper covers both Entry and Text widgets."""
    from desktop._clipboard_menu import _do_select_all

    entry = tk.Entry(root)
    entry.insert(0, "hello world")
    _do_select_all(entry)
    # Selection range covers the whole entry.
    assert entry.selection_present()
    assert entry.index("sel.first") == 0
    assert entry.index("sel.last") == len("hello world")


def test_menu_font_set(root) -> None:
    """Menu font defaults to a readable 11pt — the system 9pt is too small
    against customtkinter's larger UI."""
    from desktop._clipboard_menu import MENU_FONT, attach_clipboard_menu

    entry = tk.Entry(root)
    menu = attach_clipboard_menu(entry)
    assert MENU_FONT[1] >= 10
    # tk Menu reports configured font.
    assert menu.cget("font")
