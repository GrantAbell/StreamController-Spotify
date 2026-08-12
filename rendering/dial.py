"""Stream Deck+ dial layouts.

A dial slice is wide and short, which is a different design problem from a key
— these are laid out for that shape rather than being enlarged key art. Text
sits beside the artwork or the glyph, never on top of it, and the bottom edge
belongs to the progress or level bar so the eye always finds it in the same
place.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from . import theme
from .common import (
    draw_clipped_text,
    draw_progress_bar,
    fit_font,
    new_canvas,
    paste_artwork,
    text_size,
)
from .icons import paste_icon


@dataclass
class DialRender:
    image: Image.Image
    title_overflow: float = 0.0


def render_playback_dial(
    size: tuple[int, int],
    *,
    track_name: str,
    artist: str,
    is_playing: bool,
    fraction: float | None = None,
    position_text: str = "",
    duration_text: str = "",
    artwork: Image.Image | None = None,
    seek_hint: str | None = None,
    show_artist: bool = True,
    show_progress: bool = True,
    title_offset: int = 0,
) -> DialRender:
    width, height = size
    image, draw = new_canvas(size)

    margin = max(4, int(width * 0.035))
    bar_height = max(3, int(height * 0.06))
    bar_top = height - margin - bar_height if show_progress else height - margin

    # Artwork gets a square column on the left, deliberately well under a third
    # of the slice: the text is what the user actually reads while a song plays.
    art_size = min(int(height * 0.62), int(width * 0.30))
    content_left = margin
    if artwork is not None:
        paste_artwork(image, artwork, (margin, margin, margin + art_size, margin + art_size))
        content_left = margin + art_size + int(width * 0.04)

    glyph_size = int(height * 0.18)
    paste_icon(
        image,
        "pause" if is_playing else "play",
        glyph_size,
        (content_left + glyph_size // 2, margin + int(height * 0.11)),
        theme.SPOTIFY_GREEN if is_playing else theme.TITLE,
    )

    title_left = content_left + glyph_size + int(width * 0.02)
    title_right = width - margin
    title_top = margin
    title_bottom = margin + int(height * 0.22)

    title_font = theme.font(max(10, int(height * 0.18)), bold=True)
    full_width = draw_clipped_text(
        image,
        track_name,
        (title_left, title_top, title_right, title_bottom),
        title_font,
        theme.WHITE,
        offset_x=title_offset,
        align_center=False,
    )
    title_overflow = max(0.0, full_width - (title_right - title_left))

    # Second line: artist on the left, elapsed/total on the right — or the seek
    # readout, which replaces it while push-rotate is active.
    line_top = title_bottom + int(height * 0.06)
    line_height = max(10, int(height * 0.18))
    line_font = theme.font(max(9, int(height * 0.145)))

    if seek_hint:
        hint_font = theme.font(max(10, int(line_height * 0.9)), bold=True)
        draw.text((content_left, line_top), seek_hint, font=hint_font, fill=theme.SPOTIFY_GREEN)
    else:
        # With artwork on the left there is not enough width for both times and
        # a readable artist name, and the artist is the more useful of the two.
        if position_text and duration_text and artwork is None:
            time_text = f"{position_text} / {duration_text}"
        else:
            time_text = position_text or ""

        time_width = text_size(draw, time_text, line_font)[0] if time_text else 0
        if time_text:
            draw.text((width - margin - time_width, line_top), time_text, font=line_font, fill=theme.MUTED)

        if show_artist and artist:
            artist_right = width - margin - time_width - (int(width * 0.03) if time_text else 0)
            draw_clipped_text(
                image,
                artist,
                (content_left, line_top, max(content_left + 10, artist_right), line_top + line_height),
                line_font,
                theme.TITLE,
                align_center=False,
            )

    if show_progress:
        draw_progress_bar(draw, (margin, bar_top, width - margin, bar_top + bar_height), fraction)

    return DialRender(image=image, title_overflow=title_overflow)


def render_volume_dial(
    size: tuple[int, int],
    *,
    percent: int | None,
    muted: bool = False,
    supports_volume: bool = True,
    title: str = "VOLUME",
) -> Image.Image:
    width, height = size
    image, draw = new_canvas(size)

    margin = max(4, int(width * 0.04))
    bar_height = max(4, int(height * 0.09))
    bar_top = height - margin - bar_height

    if not supports_volume:
        glyph_size = int(height * 0.42)
        paste_icon(image, "unavailable", glyph_size, (int(width * 0.16), height // 2), theme.WARNING)
        label_font = theme.font(max(11, int(height * 0.20)), bold=True)
        draw.text((int(width * 0.30), int(height * 0.22)), title, font=label_font, fill=theme.TITLE)
        draw.text((int(width * 0.30), int(height * 0.52)), "UNAVAILABLE", font=label_font, fill=theme.WARNING)
        return image

    level = 0 if percent is None else max(0, min(100, int(percent)))
    is_muted = muted or level == 0
    color = theme.MUTED if is_muted else theme.SPOTIFY_GREEN

    glyph_size = int(height * 0.34)
    paste_icon(image, "muted" if is_muted else "volume", glyph_size, (margin + glyph_size // 2, int(height * 0.34)), color)

    label = "MUTED" if is_muted else title
    label_font = theme.font(max(10, int(height * 0.15)), bold=True)
    draw.text((margin + glyph_size + int(width * 0.03), int(height * 0.20)), label, font=label_font, fill=theme.TITLE)

    value_text = "--" if percent is None else f"{level}%"
    # Sized against the width it is allowed, so "100%" cannot grow into the
    # label beside it.
    value_font = fit_font(draw, value_text, int(width * 0.34), int(height * 0.34), bold=True)
    value_width, value_height = text_size(draw, value_text, value_font)
    _, top_offset, _, _ = draw.textbbox((0, 0), value_text, font=value_font)
    draw.text(
        (width - margin - value_width, int(height * 0.34) - value_height / 2 - top_offset),
        value_text,
        font=value_font,
        fill=theme.WHITE if not is_muted else theme.MUTED,
    )

    draw_progress_bar(draw, (margin, bar_top, width - margin, bar_top + bar_height), level / 100.0, color=color)
    return image


def render_browse_dial(
    size: tuple[int, int],
    *,
    title: str,
    subtitle: str = "",
    position_text: str = "",
    artwork: Image.Image | None = None,
    hint: str | None = None,
    icon: str = "playlist_dial",
    title_offset: int = 0,
) -> DialRender:
    """The playlist and liked-songs browsers: one item, plus where you are."""
    width, height = size
    image, draw = new_canvas(size)

    margin = max(4, int(width * 0.035))
    art_size = min(int(height - 2 * margin), int(width * 0.30))

    if artwork is not None:
        paste_artwork(image, artwork, (margin, margin, margin + art_size, margin + art_size))
        content_left = margin + art_size + int(width * 0.035)
    else:
        glyph_size = int(height * 0.4)
        paste_icon(image, icon, glyph_size, (margin + glyph_size // 2, height // 2), theme.SPOTIFY_GREEN)
        content_left = margin + glyph_size + int(width * 0.035)

    title_band = int(height * 0.34)
    title_font = theme.font(max(11, int(title_band * 0.82)), bold=True)
    title_top = int(height * 0.16)

    full_width = draw_clipped_text(
        image,
        title,
        (content_left, title_top, width - margin, title_top + title_band),
        title_font,
        theme.WHITE,
        offset_x=title_offset,
        align_center=False,
    )
    title_overflow = max(0.0, full_width - (width - margin - content_left))

    line_top = title_top + title_band + int(height * 0.04)
    line_font = theme.font(max(9, int(height * 0.17)))

    if subtitle:
        draw_clipped_text(
            image,
            subtitle,
            (content_left, line_top, width - margin, line_top + int(height * 0.22)),
            line_font,
            theme.TITLE,
            align_center=False,
        )
        line_top += int(height * 0.24)

    footer = hint or position_text
    if footer:
        color = theme.SPOTIFY_GREEN if hint else theme.MUTED
        draw.text((content_left, line_top), footer, font=line_font, fill=color)

    return DialRender(image=image, title_overflow=title_overflow)
