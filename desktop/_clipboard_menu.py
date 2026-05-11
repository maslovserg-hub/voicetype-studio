"""Right-click Cut/Copy/Paste/Select-All menu for Tk text inputs.

customtkinter's ``CTkEntry`` and ``CTkTextbox`` ship without a context menu —
right-clicking does nothing out of the box. We attach a tiny ``tk.Menu`` on
``<Button-3>`` so users can paste API keys / Telegram tokens / URLs without
remembering Ctrl+V.

Two non-obvious bits:

1. **Routing through the inner tk widget.** ``CTkEntry`` is a frame that
   *contains* a ``tk.Entry`` (exposed as ``._entry``). Calling
   ``event_generate("<<Paste>>")`` on the frame does nothing — the inner
   Entry never sees the event. We unwrap to the real widget before firing.
2. **Font size.** Tk's default menu font is the system 9pt Segoe UI on
   Windows, which reads as tiny against customtkinter's larger UI. We
   force 11pt so the menu matches the rest of the app.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

# Labels are Russian-only on purpose — Studio UI is RU-only per spec.
LABELS = {
    "cut": "Вырезать",
    "copy": "Копировать",
    "paste": "Вставить",
    "select_all": "Выделить всё",
}

MENU_FONT: tuple = ("Segoe UI", 11)


def _inner_text_widget(widget: Any) -> Any:
    """Unwrap a customtkinter widget to the underlying tk Entry/Text.

    customtkinter wraps inputs in CTkFrame; the actual tk widget where
    virtual events fire lives at ``._entry`` (CTkEntry) or ``._textbox``
    (CTkTextbox). Falls through unchanged for vanilla tkinter widgets.
    """
    inner = getattr(widget, "_entry", None)
    if inner is not None:
        return inner
    inner = getattr(widget, "_textbox", None)
    if inner is not None:
        return inner
    return widget


def _do_event(widget: Any, virtual: str) -> None:
    """Fire a virtual clipboard event on the underlying tk widget."""
    target = _inner_text_widget(widget)
    try:
        target.focus_set()
    except Exception:
        pass
    target.event_generate(virtual)


def _do_select_all(widget: Any) -> None:
    """Select-all isn't a tk default for Entry/Text; do it explicitly."""
    target = _inner_text_widget(widget)
    try:
        if isinstance(target, tk.Entry):
            target.select_range(0, "end")
            target.icursor("end")
            target.focus_set()
        elif isinstance(target, tk.Text):
            target.tag_add("sel", "1.0", "end-1c")
            target.mark_set("insert", "1.0")
            target.focus_set()
    except Exception:
        pass


def attach_clipboard_menu(
    widget: Any,
    *,
    paste: bool = True,
    cut: bool = True,
    select_all: bool = True,
) -> tk.Menu:
    """Bind a right-click context menu to ``widget``.

    Returns the constructed menu so callers can extend it (e.g. add a custom
    item before the separator) — most won't need to.
    """
    menu = tk.Menu(widget, tearoff=0, font=MENU_FONT)
    if cut:
        menu.add_command(
            label=LABELS["cut"],
            command=lambda: _do_event(widget, "<<Cut>>"),
        )
    menu.add_command(
        label=LABELS["copy"],
        command=lambda: _do_event(widget, "<<Copy>>"),
    )
    if paste:
        menu.add_command(
            label=LABELS["paste"],
            command=lambda: _do_event(widget, "<<Paste>>"),
        )
    if select_all:
        menu.add_separator()
        menu.add_command(
            label=LABELS["select_all"],
            command=lambda: _do_select_all(widget),
        )

    def _popup(event: tk.Event) -> None:
        # Focus the underlying entry so the virtual events target it.
        try:
            _inner_text_widget(widget).focus_set()
        except Exception:
            pass
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # Button-3 = right-click on Windows + Linux. macOS uses Button-2 for the
    # same action, but we ship Windows-only — keep it simple.
    widget.bind("<Button-3>", _popup)
    return menu
