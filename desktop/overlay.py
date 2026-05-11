"""Floating 5-bar audio-level overlay shown during dictation.

Same look/behaviour as the standalone Voice Type app, restructured around
the new "single Tk root" architecture: this widget is a :class:`tk.Toplevel`
attached to the app's main root rather than its own ``tk.Tk()``. Animation
runs via ``after()`` on the master, so there's no extra mainloop.

Thread-safety:
* :meth:`set_level` is called from the audio callback thread (sounddevice).
  It only writes a float member — Python's GIL makes the read-modify-write
  atomic for our purposes.
* :meth:`show` and :meth:`hide` queue an ``after(0, ...)`` so they're safe
  from any thread.
"""

from __future__ import annotations

import logging
import math
import sys
import tkinter as tk

logger = logging.getLogger(__name__)


def _screen_size_physical(win: tk.Misc) -> tuple[int, int]:
    """Return the primary-monitor size in physical pixels.

    Why: customtkinter calls ``SetProcessDpiAwareness(2)`` after Tcl is
    already initialized, so Tk caches *logical* screen dimensions while
    ``wm geometry +x+y`` is interpreted in *physical* pixels — placing
    bottom-center math at upper-left on a HiDPI screen. Querying Windows
    directly bypasses Tk's stale cache.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    return win.winfo_screenwidth(), win.winfo_screenheight()


class Overlay:
    """Pill-shaped translucent indicator with five animated bars."""

    W: int = 72
    H: int = 38
    TRANSP: str = "#010101"
    BG: str = "#1a1a35"
    ACCENT: str = "#ff6b00"
    GRAY: str = "#3a3a60"
    N_BARS: int = 5
    BAR_W: int = 3

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.TRANSP)
        self.win.wm_attributes("-transparentcolor", self.TRANSP)
        self.win.wm_attributes("-alpha", 0.70)
        self.win.withdraw()

        self.cv = tk.Canvas(
            self.win,
            width=self.W,
            height=self.H,
            bg=self.TRANSP,
            highlightthickness=0,
        )
        self.cv.pack()

        self.bars: list[int] = []
        total = self.N_BARS * self.BAR_W + (self.N_BARS - 1) * 5
        x0 = (self.W - total) // 2
        for i in range(self.N_BARS):
            x = x0 + i * (self.BAR_W + 5)
            cy = self.H // 2
            b = self.cv.create_rectangle(
                x, cy - 1, x + self.BAR_W, cy + 1,
                fill=self.GRAY, outline="", tags="bar",
            )
            self.bars.append(b)

        self.cv.bind("<ButtonPress-1>", self._drag_start)
        self.cv.bind("<B1-Motion>", self._drag_move)
        self._drag_x = 0
        self._drag_y = 0

        # Bottom-center, ~64 px above the taskbar.
        sw, sh = _screen_size_physical(self.win)
        self.win.geometry(
            f"{self.W}x{self.H}+{(sw - self.W) // 2}+{sh - self.H - 64}"
        )

        self._mic_active = False
        self._level = 0.0
        self._phase = 0.0
        self._anim_id: str | None = None
        self._schedule_anim()

    def set_level(self, rms: float) -> None:
        """Update the current audio RMS — safe to call from any thread."""
        self._level = float(rms)

    def show(self) -> None:
        self.master.after(0, self._show)

    def hide(self) -> None:
        self.master.after(0, self._hide)

    def destroy(self) -> None:
        if self._anim_id:
            try:
                self.master.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        try:
            self.win.destroy()
        except Exception:
            pass

    # --- internal --------------------------------------------------------

    def _show(self) -> None:
        self._mic_active = True
        self.win.deiconify()

    def _hide(self) -> None:
        self._mic_active = False
        self._level = 0.0
        self.win.withdraw()

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_x, self._drag_y = event.x_root, event.y_root

    def _drag_move(self, event: tk.Event) -> None:
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self.win.geometry(
            f"+{self.win.winfo_x() + dx}+{self.win.winfo_y() + dy}"
        )
        self._drag_x, self._drag_y = event.x_root, event.y_root

    def _schedule_anim(self) -> None:
        self._anim_id = self.master.after(45, self._anim)

    def _anim(self) -> None:
        self._phase += 0.25
        mh = self.H // 2 - 4
        total = self.N_BARS * self.BAR_W + (self.N_BARS - 1) * 5
        x0 = (self.W - total) // 2
        for i, bar_id in enumerate(self.bars):
            x = x0 + i * (self.BAR_W + 5)
            cy = self.H // 2
            if self._mic_active:
                lvl = min(self._level * 22 + 0.18, 1.0)
                h = max(2, int(mh * lvl * abs(math.sin(self._phase + i * 1.2))))
                color = self.ACCENT
            else:
                h = 2
                color = self.GRAY
            self.cv.coords(bar_id, x, cy - h, x + self.BAR_W, cy + h)
            self.cv.itemconfig(bar_id, fill=color)
        self._schedule_anim()
