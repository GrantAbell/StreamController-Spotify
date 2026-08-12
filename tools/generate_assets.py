"""Regenerate the SVG icons and the store thumbnail from the icon geometry.

Run after changing anything in rendering/icons.py:

    python3 tools/generate_assets.py

The SVGs are what StreamController shows in its action list; the PNG is the
store thumbnail. Both come from the same definitions as the key art, so the UI
and the deck can never disagree about what an action looks like.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_package():
    spec = importlib.util.spec_from_file_location(
        "spotify_essentials", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["spotify_essentials"] = module
    spec.loader.exec_module(module)


_load_package()

from spotify_essentials.rendering import icons, theme  # noqa: E402
from spotify_essentials.rendering.common import new_canvas  # noqa: E402
from spotify_essentials.rendering.icons import paste_icon  # noqa: E402


def write_svgs() -> int:
    out_dir = ROOT / "assets" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in icons.icon_names():
        svg = icons.svg_for(name, theme.SPOTIFY_GREEN, background=theme.BLACK)
        (out_dir / f"{name}.svg").write_text(svg, encoding="utf-8")

    return len(icons.icon_names())


def write_thumbnail() -> Path:
    """A plain, original mark: never a copy of the Spotify logo."""
    size = (640, 360)
    image, draw = new_canvas(size, theme.BLACK)

    draw.rounded_rectangle((40, 40, 600, 320), radius=28, outline=theme.SURFACE, width=3)

    paste_icon(image, "playback_dial", 150, (170, 150), theme.SPOTIFY_GREEN, theme.BLACK)
    paste_icon(image, "volume_dial", 96, (330, 150), theme.WHITE, theme.BLACK)
    paste_icon(image, "playlist_dial", 96, (450, 150), theme.WHITE, theme.BLACK)

    font = theme.font(46, bold=True)
    draw.text((170, 240), "Deck Essentials", font=font, fill=theme.WHITE)
    subtitle = theme.font(28)
    draw.text((170, 292), "for Spotify", font=subtitle, fill=theme.SPOTIFY_GREEN)

    out = ROOT / "store" / "thumbnail.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


if __name__ == "__main__":
    count = write_svgs()
    thumbnail = write_thumbnail()
    print(f"wrote {count} SVG icons to assets/icons/")
    print(f"wrote {thumbnail.relative_to(ROOT)}")
