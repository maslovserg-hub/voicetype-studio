"""Hand-rolled PIL icons used by the desktop UI.

Default emoji glyphs render unevenly in customtkinter — Tk falls back
through whatever fonts are available, and on a vanilla Windows install the
result is a tiny monochrome rectangle that doesn't read as an icon. We draw
the few buttons we care about ourselves: clean, ASCII-only, scales with
DPI, no font dependency.
"""

from __future__ import annotations

from typing import Tuple

from PIL import Image, ImageDraw


_RGBA = Tuple[int, int, int, int]


def _supersample_canvas(size: int, factor: int = 3) -> Tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Allocate a ``size*factor`` canvas. Caller draws on it, then we
    downsample with LANCZOS for smooth edges."""
    s = size * factor
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img), s


def make_attach_icon(
    size: int = 22,
    color: _RGBA = (255, 255, 255, 235),
) -> Image.Image:
    """Document outline with a centred ``+`` — universally legible as
    «выбрать / добавить файл». Returned as a PIL image; pass through
    :func:`as_ctk_image` for use in customtkinter buttons.
    """
    img, d, s = _supersample_canvas(size, factor=3)
    stroke = max(2, s // 22)

    # Document outline: rounded rect with a folded top-right corner.
    pad = s // 6
    fold = s // 5  # how much of the corner is folded
    # Main body — open polygon so we can punch the fold cleanly.
    body = [
        (pad, pad),                 # top-left
        (s - pad - fold, pad),      # start of fold
        (s - pad, pad + fold),      # fold tip
        (s - pad, s - pad),         # bottom-right
        (pad, s - pad),             # bottom-left
        (pad, pad),                 # back to start
    ]
    d.line(body, fill=color, width=stroke, joint="curve")
    # The fold's inner edge (the small triangle on top-right).
    d.line(
        [(s - pad - fold, pad), (s - pad - fold, pad + fold), (s - pad, pad + fold)],
        fill=color, width=stroke, joint="curve",
    )

    # Centred ``+``.
    cx, cy = s // 2, s // 2 + s // 16  # nudge below the fold
    arm = s // 7
    d.line([(cx - arm, cy), (cx + arm, cy)], fill=color, width=stroke + 1)
    d.line([(cx, cy - arm), (cx, cy + arm)], fill=color, width=stroke + 1)

    return img.resize((size, size), Image.LANCZOS)


def as_ctk_image(pil_image: Image.Image, size: int):
    """Wrap a PIL image into a ``CTkImage``. Imported lazily so the icons
    module itself stays pull-able from non-GUI contexts (tests, headless)."""
    import customtkinter as ctk

    return ctk.CTkImage(
        light_image=pil_image,
        dark_image=pil_image,
        size=(size, size),
    )
