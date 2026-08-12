"""How every non-normal condition looks.

The reference plugin's most useful habit is that it never fails silently: if
Spotify is unreachable, rate limiting, or simply has no device to talk to, the
key says so. These renderers are what make that true here.
"""

from __future__ import annotations

from PIL import Image

from . import theme
from .common import draw_centered_text, fit_font, new_canvas, size_for
from .icons import STATUS_ICONS, paste_icon

#: Short enough to fit on a 72px key without shrinking to unreadable.
STATUS_LABELS = {
    "success": "DONE",
    "pending": "…",
    "busy": "BUSY",
    "rate_limited": "RATE\nLIMITED",
    "unavailable": "UNAVAIL",
    "no_device": "NO\nDEVICE",
    "auth_required": "SET UP",
    "api_error": "ERROR",
    "offline": "OFFLINE",
    "unknown": "?",
}

#: Statuses that mean "this action cannot do its job right now".
BLOCKING_STATUSES = frozenset(
    {"auth_required", "no_device", "rate_limited", "unavailable", "api_error", "offline"}
)


def is_blocking(status) -> bool:
    return _name(status) in BLOCKING_STATUSES


def _name(status) -> str:
    return getattr(status, "value", status)


def render_status(
    status,
    *,
    is_dial: bool = False,
    size: tuple[int, int] | None = None,
    title: str | None = None,
    detail: str | None = None,
) -> Image.Image:
    """The standard status card, on either a key or a dial."""
    name = _name(status)
    canvas_size = size_for(is_dial, size)
    width, height = canvas_size
    color = theme.status_color(name)

    image, draw = new_canvas(canvas_size)
    label = detail or STATUS_LABELS.get(name, name.upper())
    lines = [part for part in label.split("\n") if part]

    if is_dial:
        glyph_size = int(height * 0.5)
        paste_icon(image, STATUS_ICONS.get(name, "unknown"), glyph_size, (int(width * 0.16), height // 2), color)

        text_left = int(width * 0.30)
        text_width = width - text_left - int(width * 0.06)
        band = height / max(2, len(lines) + (1 if title else 0))
        top = (height - band * (len(lines) + (1 if title else 0))) / 2

        if title:
            font = fit_font(draw, title, text_width, int(band * 0.62), bold=True)
            draw.text((text_left, top), title.upper(), font=font, fill=theme.TITLE)
            top += band

        for line in lines:
            font = fit_font(draw, line, text_width, int(band * 0.78), bold=True)
            draw.text((text_left, top), line, font=font, fill=color)
            top += band

        return image

    title_band = int(height * 0.16) if title else 0
    text_band = int(height * 0.22) * len(lines)
    glyph_area = height - title_band - text_band

    paste_icon(
        image,
        STATUS_ICONS.get(name, "unknown"),
        int(min(width, glyph_area) * 0.60),
        (width // 2, title_band + glyph_area // 2),
        color,
    )

    if title:
        font = fit_font(draw, title.upper(), int(width * 0.9), int(title_band * 0.8), bold=True)
        draw_centered_text(draw, title.upper(), width / 2, int(title_band * 0.12), font, theme.MUTED)

    top = height - text_band
    for line in lines:
        font = fit_font(draw, line, int(width * 0.9), int(height * 0.19), bold=True)
        draw_centered_text(draw, line, width / 2, top, font, color)
        top += int(height * 0.22)

    return image


def render_success_overlay(size: tuple[int, int], text: str = "DONE") -> Image.Image:
    """The brief confirmation shown after Like, Add to Playlist, Copy, Transfer.

    Returned as RGBA, opaque: overlays are composited with the image itself as
    the transparency mask, which Pillow rejects outright for an RGB image.
    """
    width, height = size
    image, draw = new_canvas(size, theme.GREEN_DIM)

    glyph_size = int(min(width, height) * 0.36)
    paste_icon(image, "success", glyph_size, (width // 2, int(height * 0.36)), theme.WHITE, theme.GREEN_DIM)

    font = fit_font(draw, text, int(width * 0.9), int(height * 0.24), bold=True)
    draw_centered_text(draw, text, width / 2, int(height * 0.60), font, theme.WHITE)
    return image.convert("RGBA")
