"""The Song Stack key: a whole now-playing surface in one key slot.

The layout is built from bands rather than fixed coordinates, so switching a
section off in the settings gives its space to the sections that are left
instead of leaving a hole. Album art gets a band of its own and nothing is ever
drawn over it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from . import theme
from .common import draw_clipped_text, draw_progress_bar, new_canvas, paste_artwork
from .icons import paste_icon


@dataclass
class SongRender:
    image: Image.Image
    #: How far the text exceeds its window, so the caller can drive the marquee.
    title_overflow: float = 0.0
    artist_overflow: float = 0.0


def render_song_stack(
    size: tuple[int, int],
    *,
    track_name: str,
    artist: str,
    artwork: Image.Image | None = None,
    is_playing: bool = False,
    fraction: float | None = None,
    like_state: str = "unknown",
    explicit: bool = False,
    show_artwork: bool = True,
    show_title: bool = True,
    show_artist: bool = True,
    show_progress: bool = True,
    show_like_state: bool = True,
    show_explicit: bool = True,
    title_offset: int = 0,
    artist_offset: int = 0,
) -> SongRender:
    width, height = size
    image, draw = new_canvas(size)

    show_badges = show_like_state or show_explicit
    margin = max(3, int(width * 0.05))

    # -- band allocation --------------------------------------------------

    status_band = int(height * 0.15) if show_badges else int(height * 0.12)
    progress_band = int(height * 0.10) if show_progress else 0
    remaining = height - status_band - progress_band

    weights: list[tuple[str, float]] = []
    if show_artwork:
        weights.append(("artwork", 3.0))
    if show_title:
        weights.append(("title", 1.15))
    if show_artist:
        weights.append(("artist", 0.95))

    if not weights:
        # Everything is switched off; a lone transport glyph is still honest
        # about what the key does.
        paste_icon(image, "pause" if is_playing else "play", int(min(width, height) * 0.5), (width // 2, height // 2), theme.SPOTIFY_GREEN)
        return SongRender(image=image)

    total_weight = sum(weight for _, weight in weights)
    bands: dict[str, tuple[int, int]] = {}
    cursor = status_band
    for name, weight in weights:
        band_height = int(remaining * (weight / total_weight))
        bands[name] = (cursor, cursor + band_height)
        cursor += band_height

    # -- status row: transport, explicit, liked ---------------------------

    glyph_size = int(status_band * 0.82)
    paste_icon(
        image,
        "pause" if is_playing else "play",
        glyph_size,
        (margin + glyph_size // 2, status_band // 2),
        theme.SPOTIFY_GREEN if is_playing else theme.TITLE,
    )

    badge_x = width - margin
    if show_like_state and like_state in ("liked", "not_liked", "busy"):
        badge_size = int(status_band * 0.86)
        badge_color = {
            "liked": theme.SPOTIFY_GREEN,
            "not_liked": theme.MUTED,
            "busy": theme.TITLE,
        }[like_state]
        badge_icon = "library_saved" if like_state == "liked" else "library_add"
        paste_icon(image, badge_icon, badge_size, (badge_x - badge_size // 2, status_band // 2), badge_color)
        badge_x -= badge_size + max(2, int(width * 0.02))

    if show_explicit and explicit:
        badge_size = int(status_band * 0.78)
        paste_icon(image, "explicit", badge_size, (badge_x - badge_size // 2, status_band // 2), theme.EXPLICIT_BG)

    # -- artwork ----------------------------------------------------------

    if "artwork" in bands:
        top, bottom = bands["artwork"]
        paste_artwork(image, artwork, (margin, top, width - margin, bottom))

    # -- text -------------------------------------------------------------

    title_overflow = 0.0
    artist_overflow = 0.0
    text_window = (margin, width - margin)

    if "title" in bands and track_name:
        top, bottom = bands["title"]
        # Without artwork there is room for a genuinely large title, which is
        # the point of turning artwork off.
        font = theme.font(_text_size_for(bottom - top, width, scale=0.10 if show_artwork else 0.14), bold=True)
        full_width = draw_clipped_text(
            image,
            track_name,
            (text_window[0], top, text_window[1], bottom),
            font,
            theme.WHITE,
            offset_x=title_offset,
        )
        title_overflow = max(0.0, full_width - (text_window[1] - text_window[0]))

    if "artist" in bands and artist:
        top, bottom = bands["artist"]
        font = theme.font(_text_size_for(bottom - top, width, scale=0.085))
        full_width = draw_clipped_text(
            image,
            artist,
            (text_window[0], top, text_window[1], bottom),
            font,
            theme.TITLE,
            offset_x=artist_offset,
        )
        artist_overflow = max(0.0, full_width - (text_window[1] - text_window[0]))

    # -- progress ---------------------------------------------------------

    if show_progress:
        bar_height = max(3, int(progress_band * 0.34))
        bar_top = height - progress_band + (progress_band - bar_height) / 2
        draw_progress_bar(draw, (margin, bar_top, width - margin, bar_top + bar_height), fraction)

    return SongRender(image=image, title_overflow=title_overflow, artist_overflow=artist_overflow)


def _text_size_for(band_height: int, key_width: int, scale: float = 0.10) -> int:
    """Point size for a text band.

    Capped against the key width as well as the band, because a tall band on a
    large key would otherwise pick a size at which almost every song title
    overflows and has to scroll.
    """
    return max(9, min(int(band_height * 0.78), int(key_width * scale)))


def render_compact_now_playing(
    size: tuple[int, int],
    *,
    track_name: str,
    artist: str,
    is_playing: bool,
    fraction: float | None,
    title_offset: int = 0,
) -> SongRender:
    """The no-artwork fallback: title, artist and progress only.

    Used when the key is too small for artwork to be worth the space it costs.
    """
    return render_song_stack(
        size,
        track_name=track_name,
        artist=artist,
        artwork=None,
        is_playing=is_playing,
        fraction=fraction,
        show_artwork=False,
        show_like_state=False,
        show_explicit=False,
        title_offset=title_offset,
    )
