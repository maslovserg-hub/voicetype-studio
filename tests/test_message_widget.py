"""Tests for ``desktop._message_widget`` collapse/expand machinery.

Needs a real Tk root (customtkinter is built on tkinter); these tests
get skipped if ``CTk()`` can't be constructed in the current environment.
"""

from __future__ import annotations

import pytest


def _is_packed(widget) -> bool:
    """Tk's ``pack_info`` raises ``TclError`` for unpacked widgets instead
    of returning an empty dict, so wrap the call."""
    try:
        return bool(widget.pack_info())
    except Exception:
        return False


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


def _make_widget(parent, task_id: str = "t"):
    from desktop._message_widget import MessageWidget

    return MessageWidget(
        parent,
        task_id=task_id,
        source_label="📎 demo.mp3",
        on_format_click=lambda *a: None,
    )


# --- public API surface (no Tk needed) -----------------------------------


def test_message_widget_has_collapse_api() -> None:
    from desktop._message_widget import MessageWidget

    for method in ("collapse", "expand", "toggle", "is_collapsed"):
        assert callable(getattr(MessageWidget, method, None)), method


def test_widget_state_alias_exported() -> None:
    from desktop import _message_widget

    assert hasattr(_message_widget, "WidgetState")


# --- live behavior --------------------------------------------------------


def test_collapse_expand_cycle(ctk_root) -> None:
    w = _make_widget(ctk_root)
    assert w.is_collapsed() is False

    w.collapse()
    assert w.is_collapsed() is True

    w.expand()
    assert w.is_collapsed() is False

    w.toggle()
    assert w.is_collapsed() is True
    w.toggle()
    assert w.is_collapsed() is False


def test_collapse_idempotent(ctk_root) -> None:
    w = _make_widget(ctk_root)
    w.collapse()
    w.collapse()  # no-op the second time
    assert w.is_collapsed() is True


def test_state_transitions(ctk_root) -> None:
    w = _make_widget(ctk_root)
    assert w._state == "processing"

    w.mark_done()
    assert w._state == "done"

    w2 = _make_widget(ctk_root, task_id="t2")
    w2.mark_error("oops")
    assert w2._state == "error"


def test_collapsed_mark_done_keeps_buttons_hidden(ctk_root) -> None:
    """Calling mark_done() on a collapsed widget updates state but
    must not pop the format-button row open behind the user's back."""
    w = _make_widget(ctk_root)
    w.collapse()
    w.mark_done()

    assert w.is_collapsed() is True
    assert _is_packed(w._buttons_frame) is False

    # Expanding restores the body.
    w.expand()
    assert _is_packed(w._buttons_frame) is True


def test_collapsed_mark_error_keeps_message_hidden(ctk_root) -> None:
    w = _make_widget(ctk_root)
    w.collapse()
    w.mark_error("boom")

    assert w._state == "error"
    assert w._error_label is not None
    assert _is_packed(w._error_label) is False

    w.expand()
    assert _is_packed(w._error_label) is True
