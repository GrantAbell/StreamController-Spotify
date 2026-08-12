"""Colours, fonts and sizes — the one place the visual language is defined.

Every colour the plugin draws comes from here so the whole action set stays
consistent, and so the brand green can be corrected in one edit if Spotify's
current brand assets ever move.
"""

from __future__ import annotations

import glob
import os
from functools import lru_cache

from PIL import ImageFont

# -- palette ---------------------------------------------------------------

SPOTIFY_GREEN = (30, 215, 96)
SPOTIFY_DARK = (25, 20, 20)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

BACKGROUND = BLACK
SURFACE = (18, 18, 18)
TITLE = (179, 179, 179)
MUTED = (110, 110, 110)
TRACK = (58, 58, 58)

GREEN_DIM = (23, 140, 65)
WARNING = (255, 176, 46)
ERROR = (226, 87, 76)
INFO = (120, 170, 255)

#: Explicit-content marker; deliberately neutral, never green.
EXPLICIT_BG = (140, 140, 140)

# -- geometry --------------------------------------------------------------

#: Fallbacks only. Real sizes come from `ControllerInput.get_image_size()`.
KEY_SIZE = (144, 144)
DIAL_SIZE = (200, 100)

#: Icons are authored on this grid; see rendering/icons.py.
ICON_GRID = 24.0
ICON_STROKE = 2.0

#: Supersampling factor for glyph drawing. Pillow has no antialiased shape
#: rendering, so shapes are drawn large and reduced.
SUPERSAMPLE = 4

# -- fonts -----------------------------------------------------------------

_REGULAR_CANDIDATES = (
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/app/share/fonts/DejaVuSans.ttf",
    "/usr/share/fonts/Adwaita/AdwaitaSans-Regular.ttf",
)

_BOLD_CANDIDATES = (
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/app/share/fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/Adwaita/AdwaitaSans-Bold.ttf",
)


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> str | None:
    for path in (_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES):
        if os.path.exists(path):
            return path

    # Last resort: whatever sans the system does have, so a deck is never blank.
    patterns = ("/usr/share/fonts/**/*Bold.ttf", "/usr/share/fonts/**/*.ttf") if bold else ("/usr/share/fonts/**/*.ttf",)
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return sorted(matches)[0]
    return None


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, max(6, int(size)))
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=max(6, int(size)))
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def status_color(name: str) -> tuple[int, int, int]:
    """Colour for a status glyph, by `ActionStatus` value name."""
    return {
        "success": SPOTIFY_GREEN,
        "ready": WHITE,
        "pending": TITLE,
        "busy": TITLE,
        "rate_limited": WARNING,
        "unavailable": WARNING,
        "no_device": WARNING,
        "auth_required": WARNING,
        "api_error": ERROR,
        "offline": ERROR,
        "unknown": MUTED,
    }.get(name, WHITE)
