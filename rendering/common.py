"""Drawing helpers shared by the key, dial and Song Stack layouts.

Pillow only. Every renderer in this package is a pure function of the state it
is given, so the actions can draw from any thread and the tests can assert on
the result without a deck or a Spotify account.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from . import theme


def new_canvas(size: tuple[int, int], background=theme.BACKGROUND) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (int(size[0]), int(size[1])), background)
    return image, ImageDraw.Draw(image)


def text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return (right - left, bottom - top)


def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return text_size(draw, text, font)[0]


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, minimum: int = 9, bold: bool = False):
    """Largest font size at which `text` still fits, down to a floor."""
    size = int(start_size)
    while size > minimum:
        candidate = theme.font(size, bold)
        if text_width(draw, text, candidate) <= max_width:
            return candidate
        size -= 1
    return theme.font(minimum, bold)


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: float,
    top_y: float,
    font,
    color,
) -> int:
    """Draw text horizontally centred at `center_x`; returns its height."""
    width, height = text_size(draw, text, font)
    _, offset_top, _, _ = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - width / 2, top_y - offset_top), text, font=font, fill=color)
    return height


def shorten_to_width(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """`text` trimmed with an ellipsis until it fits.

    For labels that cannot scroll and must not run off the key — a name already
    shrunk to the smallest readable size still has to stop somewhere.
    """
    if text_width(draw, text, font) <= max_width:
        return text

    trimmed = text
    while trimmed and text_width(draw, trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed.rstrip()}…" if trimmed else "…"


def draw_clipped_text(
    image: Image.Image,
    text: str,
    box: tuple[int, int, int, int],
    font,
    color,
    offset_x: int = 0,
    align_center: bool = True,
) -> int:
    """Draw text inside a window, shifted left by `offset_x`.

    Text wider than the window is clipped rather than ellipsised, because the
    marquee scrolls it — the caller supplies the offset. Returns the full text
    width so the caller can tell whether it overflowed.
    """
    x0, y0, x1, y1 = (int(value) for value in box)
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)

    scratch = Image.new("RGB", (width, height), theme.BACKGROUND)
    scratch_draw = ImageDraw.Draw(scratch)

    full_width, _ = text_size(scratch_draw, text, font)
    _, top, _, bottom = scratch_draw.textbbox((0, 0), text, font=font)
    text_height = bottom - top

    if full_width <= width and align_center:
        start_x = (width - full_width) / 2
    else:
        start_x = -offset_x

    scratch_draw.text((start_x, (height - text_height) / 2 - top), text, font=font, fill=color)
    image.paste(scratch, (x0, y0))
    return full_width


def draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    fraction: float | None,
    color=theme.SPOTIFY_GREEN,
    track=theme.TRACK,
) -> None:
    x0, y0, x1, y1 = box
    height = max(2.0, y1 - y0)
    radius = height / 2.0

    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=track)

    if fraction is None:
        return

    filled = max(0.0, min(1.0, fraction))
    if filled <= 0:
        return

    end = x0 + (x1 - x0) * filled
    # Never draw a sliver thinner than the cap, which would render as a dot in
    # the wrong place.
    end = max(end, x0 + height)
    draw.rounded_rectangle((x0, y0, min(end, x1), y1), radius=radius, fill=color)


def fit_artwork(artwork: Image.Image, box_size: tuple[int, int]) -> Image.Image:
    """Scale artwork to fit a box, preserving its aspect ratio.

    Spotify's rules say artwork must not be cropped, stretched, or covered, so
    this only ever letterboxes — it never fills by cropping.
    """
    width, height = box_size
    copy = artwork.copy()
    copy.thumbnail((max(1, int(width)), max(1, int(height))), Image.LANCZOS)
    return copy


def paste_artwork(
    image: Image.Image,
    artwork: Image.Image | None,
    box: tuple[int, int, int, int],
    placeholder_color=theme.MUTED,
) -> None:
    """Place artwork centred in its own region, or a music-note placeholder.

    Nothing is ever drawn on top of this region afterwards.
    """
    from .icons import paste_icon

    x0, y0, x1, y1 = (int(value) for value in box)
    width, height = max(1, x1 - x0), max(1, y1 - y0)
    center = (x0 + width // 2, y0 + height // 2)

    if artwork is None:
        paste_icon(image, "music_note", int(min(width, height) * 0.7), center, placeholder_color)
        return

    fitted = fit_artwork(artwork, (width, height))
    image.paste(fitted, (int(center[0] - fitted.width / 2), int(center[1] - fitted.height / 2)))


def size_for(is_dial: bool, size: tuple[int, int] | None = None) -> tuple[int, int]:
    """The real input size when StreamController supplied one, else a default."""
    if size and size[0] and size[1]:
        return (int(size[0]), int(size[1]))
    return theme.DIAL_SIZE if is_dial else theme.KEY_SIZE
