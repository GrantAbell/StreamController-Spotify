"""Key layouts: a glyph, optionally a value, optionally a state marker.

Deliberately plain. A Stream Deck key is small and is read at a glance, so every
layout here is one dominant glyph with at most one line of supporting text, and
the on/off distinction is carried by colour plus a marker rather than colour
alone.
"""

from __future__ import annotations

from PIL import Image

from . import theme
from .common import draw_centered_text, fit_font, new_canvas
from .icons import paste_icon


def render_glyph_key(
    size: tuple[int, int],
    icon: str,
    *,
    color=theme.WHITE,
    caption: str | None = None,
    caption_color=None,
    active: bool = False,
    dim: bool = False,
    background=theme.BACKGROUND,
) -> Image.Image:
    """One centred glyph, with an optional caption and an active marker."""
    width, height = size
    image, draw = new_canvas(size, background)

    glyph_color = theme.MUTED if dim else color

    has_caption = bool(caption)
    # An "on" state is shown either by the caption turning green or, when there
    # is no caption, by a marker under the glyph — never both, which reads as
    # clutter on a 72px key.
    has_marker = active and not has_caption

    if caption_color is None:
        if active:
            caption_color = theme.SPOTIFY_GREEN
        else:
            caption_color = theme.MUTED if dim else theme.TITLE

    # Reserve the bands first so the glyph is centred in what is left, not in
    # the whole key — otherwise a caption pushes it visually off-centre.
    caption_band = int(height * 0.22) if has_caption else 0
    marker_band = int(height * 0.09) if has_marker else 0
    glyph_area_height = height - caption_band - marker_band

    glyph_size = int(min(width, glyph_area_height) * 0.62)
    paste_icon(image, icon, glyph_size, (width // 2, marker_band + glyph_area_height // 2), glyph_color, background)

    if has_marker:
        marker_width = max(8, int(width * 0.22))
        marker_height = max(3, int(height * 0.035))
        top = height - caption_band - marker_band + int(marker_band * 0.25)
        draw.rounded_rectangle(
            (
                (width - marker_width) / 2,
                top,
                (width + marker_width) / 2,
                top + marker_height,
            ),
            radius=marker_height / 2,
            fill=theme.SPOTIFY_GREEN,
        )

    if has_caption:
        from .common import shorten_to_width

        text_width = int(width * 0.86)
        font = fit_font(draw, caption, text_width, int(caption_band * 0.66), bold=True)
        # Captions are mostly short words, but an action can put a name here.
        text = shorten_to_width(draw, caption, font, text_width)
        draw_centered_text(draw, text, width / 2, height - caption_band + int(caption_band * 0.1), font, caption_color)

    return image


def render_artwork_key(
    size: tuple[int, int],
    artwork: Image.Image | None,
    *,
    caption: str | None = None,
    caption_color=theme.WHITE,
) -> Image.Image:
    """Cover art with its name underneath, for a key that stands for one thing.

    The caption gets a reserved band rather than an overlay: Spotify's rules say
    artwork must not be cropped, stretched or covered, so nothing is drawn on
    top of it and it is only ever letterboxed into the space that is left.

    A name is shrunk to fit and then ellipsised, because it cannot scroll here
    and a key that spills its label off both edges reads as broken.
    """
    from .common import paste_artwork, shorten_to_width

    width, height = size
    image, draw = new_canvas(size)

    caption_band = int(height * 0.20) if caption else 0
    margin = max(2, int(min(width, height) * 0.05))

    paste_artwork(image, artwork, (margin, margin, width - margin, height - caption_band - margin))

    if caption:
        text_width = int(width * 0.92)
        font = fit_font(draw, caption, text_width, int(caption_band * 0.72), bold=True)
        text = shorten_to_width(draw, caption, font, text_width)
        draw_centered_text(draw, text, width / 2, height - caption_band + int(caption_band * 0.08), font, caption_color)

    return image


def render_value_key(
    size: tuple[int, int],
    icon: str,
    value: str,
    *,
    color=theme.SPOTIFY_GREEN,
    value_color=theme.WHITE,
    fraction: float | None = None,
    caption: str | None = None,
) -> Image.Image:
    """A glyph over a large value, used by the volume keys.

    The bar is what makes the level readable without reading the number.
    """
    from .common import draw_progress_bar

    width, height = size
    image, draw = new_canvas(size)

    bar_height = max(4, int(height * 0.05))
    bar_bottom = height - int(height * 0.12)
    caption_band = int(height * 0.16) if caption else 0

    glyph_size = int(min(width, height) * 0.36)
    paste_icon(image, icon, glyph_size, (width // 2, int(height * 0.28)), color)

    font = fit_font(draw, value, int(width * 0.86), int(height * 0.34), bold=True)
    draw_centered_text(draw, value, width / 2, int(height * 0.46), font, value_color)

    if fraction is not None:
        margin = int(width * 0.12)
        draw_progress_bar(
            draw,
            (margin, bar_bottom - bar_height - caption_band, width - margin, bar_bottom - caption_band),
            fraction,
            color=color,
        )

    if caption:
        caption_font = fit_font(draw, caption, int(width * 0.9), int(caption_band * 0.8), bold=True)
        draw_centered_text(draw, caption, width / 2, height - caption_band, caption_font, theme.TITLE)

    return image


def render_mode_stack_key(
    size: tuple[int, int],
    *,
    shuffle: bool | None,
    repeat_mode: str | None,
) -> Image.Image:
    """All three playback modes on one key, as three labelled rows.

    Showing every mode is the point of this action: the user has to be able to
    tell what shuffle and repeat are doing without pressing anything.
    """
    width, height = size
    image, draw = new_canvas(size)

    rows = (
        ("SHUFFLE", bool(shuffle), shuffle is None),
        ("CONTEXT", repeat_mode == "context", repeat_mode is None),
        ("TRACK", repeat_mode == "track", repeat_mode is None),
    )

    top = int(height * 0.16)
    row_height = int((height - top - int(height * 0.10)) / 3)
    left = int(width * 0.12)
    pip_x = width - int(width * 0.18)
    pip_radius = max(3, int(min(width, height) * 0.045))

    label_font = fit_font(draw, "SHUFFLE", int(width * 0.58), int(row_height * 0.62), bold=True)

    for index, (label, is_on, is_unknown) in enumerate(rows):
        center_y = top + int(row_height * (index + 0.5))

        if is_unknown:
            label_color, pip_fill, pip_outline = theme.MUTED, None, theme.MUTED
        elif is_on:
            label_color, pip_fill, pip_outline = theme.WHITE, theme.SPOTIFY_GREEN, theme.SPOTIFY_GREEN
        else:
            label_color, pip_fill, pip_outline = theme.MUTED, None, theme.TRACK

        _, top_offset, _, bottom_offset = draw.textbbox((0, 0), label, font=label_font)
        draw.text((left, center_y - (bottom_offset - top_offset) / 2 - top_offset), label, font=label_font, fill=label_color)

        box = (pip_x - pip_radius, center_y - pip_radius, pip_x + pip_radius, center_y + pip_radius)
        if pip_fill:
            draw.ellipse(box, fill=pip_fill)
        else:
            draw.ellipse(box, outline=pip_outline, width=max(1, pip_radius // 3))

    return image


def render_text_key(
    size: tuple[int, int],
    lines: list[str],
    *,
    color=theme.WHITE,
    accent: str | None = None,
    accent_color=theme.SPOTIFY_GREEN,
) -> Image.Image:
    """A short stack of text lines, for the information-only actions."""
    width, height = size
    image, draw = new_canvas(size)

    entries = ([(accent, accent_color)] if accent else []) + [(text, color) for text in lines if text]
    if not entries:
        return image

    band = (height * 0.84) / len(entries)
    top = height * 0.08

    for index, (text, text_color) in enumerate(entries):
        font = fit_font(draw, text, int(width * 0.9), int(band * 0.8), bold=(index == 0))
        draw_centered_text(draw, text, width / 2, top + band * index + band * 0.1, font, text_color)

    return image
