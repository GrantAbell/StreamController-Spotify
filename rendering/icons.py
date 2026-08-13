"""The plugin's original icon set, defined once as geometry.

Every glyph is authored on the same 24-unit grid with the same stroke weight, so
the whole action set looks like one family. The geometry is data rather than
drawing code, which lets the same definitions produce both the Pillow images
painted onto keys and dials and the SVG files used for the action list in
StreamController's UI — the two can never drift apart.

Nothing here is derived from another plugin's assets.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw

from .theme import ICON_GRID, ICON_STROKE, SUPERSAMPLE

# -- primitive constructors -------------------------------------------------


def line(x1, y1, x2, y2, w=1.0, bg=False):
    return {"op": "line", "points": [(x1, y1), (x2, y2)], "w": w, "bg": bg}


def polyline(points, w=1.0, bg=False):
    return {"op": "line", "points": list(points), "w": w, "bg": bg}


def poly(points, filled=True, w=1.0, bg=False):
    return {"op": "poly", "points": list(points), "filled": filled, "w": w, "bg": bg}


def rect(x0, y0, x1, y1, r=0.0, filled=True, w=1.0, bg=False):
    return {"op": "rect", "box": (x0, y0, x1, y1), "r": r, "filled": filled, "w": w, "bg": bg}


def circle(cx, cy, radius, filled=True, w=1.0, bg=False):
    return {"op": "circle", "c": (cx, cy), "r": radius, "filled": filled, "w": w, "bg": bg}


def dot(cx, cy, radius, bg=False):
    return circle(cx, cy, radius, filled=True, bg=bg)


def arc(x0, y0, x1, y1, start, end, w=1.0, bg=False):
    return {"op": "arc", "box": (x0, y0, x1, y1), "start": start, "end": end, "w": w, "bg": bg}


# -- shared sub-shapes ------------------------------------------------------

_SPEAKER = poly([(4.5, 9.5), (8.0, 9.5), (12.5, 5.5), (12.5, 18.5), (8.0, 14.5), (4.5, 14.5)])
_SPEAKER_SMALL = poly([(2.5, 9.8), (5.8, 9.8), (10.0, 6.0), (10.0, 18.0), (5.8, 14.2), (2.5, 14.2)])
_RING = circle(12, 12, 9.0, filled=False, w=0.85)

_REPEAT_LOOP = [
    polyline([(6.0, 9.5), (6.0, 6.5), (15.5, 6.5)], w=0.95),
    poly([(14.5, 3.4), (18.5, 6.5), (14.5, 9.6)]),
    polyline([(18.0, 14.5), (18.0, 17.5), (8.5, 17.5)], w=0.95),
    poly([(9.5, 14.4), (5.5, 17.5), (9.5, 20.6)]),
]

def _SPARKLE(cx: float, cy: float, radius: float) -> dict:  # noqa: N802 - a shape, like the constants around it
    """A four-pointed star with concave sides, drawn as one filled polygon."""
    waist = radius * 0.28
    return poly(
        [
            (cx, cy - radius),
            (cx + waist, cy - waist),
            (cx + radius, cy),
            (cx + waist, cy + waist),
            (cx, cy + radius),
            (cx - waist, cy + waist),
            (cx - radius, cy),
            (cx - waist, cy - waist),
        ]
    )


_MUSIC_NOTE = [
    dot(9.4, 16.6, 2.7),
    line(12.1, 16.6, 12.1, 6.4, w=0.9),
    poly([(12.1, 6.1), (17.9, 4.5), (17.9, 8.1), (12.1, 9.6)]),
]


# -- the set ----------------------------------------------------------------

ICONS: dict[str, list[dict]] = {
    # transport
    "play": [poly([(8.0, 4.5), (19.5, 12.0), (8.0, 19.5)])],
    "pause": [
        rect(7.4, 4.5, 10.6, 19.5, r=1.2),
        rect(13.4, 4.5, 16.6, 19.5, r=1.2),
    ],
    "previous": [
        rect(4.5, 4.5, 7.0, 19.5, r=1.0),
        poly([(19.5, 4.5), (8.5, 12.0), (19.5, 19.5)]),
    ],
    "next": [
        rect(17.0, 4.5, 19.5, 19.5, r=1.0),
        poly([(4.5, 4.5), (15.5, 12.0), (4.5, 19.5)]),
    ],
    "seek_backward": [
        poly([(11.5, 5.5), (3.5, 12.0), (11.5, 18.5)]),
        poly([(20.5, 5.5), (12.5, 12.0), (20.5, 18.5)]),
    ],
    "seek_forward": [
        poly([(12.5, 5.5), (20.5, 12.0), (12.5, 18.5)]),
        poly([(3.5, 5.5), (11.5, 12.0), (3.5, 18.5)]),
    ],
    # modes
    "shuffle": [
        polyline([(3.5, 7.5), (8.0, 7.5), (15.5, 16.5), (18.0, 16.5)], w=0.95),
        polyline([(3.5, 16.5), (8.0, 16.5), (15.5, 7.5), (18.0, 7.5)], w=0.95),
        poly([(17.0, 4.6), (21.0, 7.5), (17.0, 10.4)]),
        poly([(17.0, 13.6), (21.0, 16.5), (17.0, 19.4)]),
    ],
    # Spotify's own smart shuffle mark is the shuffle arrows plus sparkles, so
    # the same shape is reused a size down to leave the corner free for them.
    "smart_shuffle": [
        polyline([(5.6, 9.3), (9.5, 9.3), (15.9, 17.1), (18.1, 17.1)], w=0.95),
        polyline([(5.6, 17.1), (9.5, 17.1), (15.9, 9.3), (18.1, 9.3)], w=0.95),
        poly([(17.2, 6.8), (20.6, 9.3), (17.2, 11.8)]),
        poly([(17.2, 14.6), (20.6, 17.1), (17.2, 19.6)]),
        _SPARKLE(5.4, 4.6, 3.0),
        _SPARKLE(10.9, 2.9, 1.7),
    ],
    "repeat_context": list(_REPEAT_LOOP),
    "repeat_track": _REPEAT_LOOP
    + [
        polyline([(10.6, 11.7), (12.2, 10.5), (12.2, 15.4)], w=0.75),
        line(10.5, 15.4, 14.0, 15.4, w=0.75),
    ],
    "mode_stack": [
        line(4.5, 7.0, 14.0, 7.0, w=0.9),
        dot(18.0, 7.0, 1.9),
        line(4.5, 12.0, 14.0, 12.0, w=0.9),
        dot(18.0, 12.0, 1.9),
        line(4.5, 17.0, 14.0, 17.0, w=0.9),
        dot(18.0, 17.0, 1.9),
    ],
    # library
    "library_add": [
        circle(12, 12, 8.6, filled=False, w=0.95),
        line(12.0, 7.6, 12.0, 16.4, w=0.95),
        line(7.6, 12.0, 16.4, 12.0, w=0.95),
    ],
    "library_saved": [
        circle(12, 12, 8.6, filled=True),
        polyline([(7.8, 12.3), (10.7, 15.2), (16.3, 9.0)], w=1.1, bg=True),
    ],
    "explicit": [
        rect(3.5, 3.5, 20.5, 20.5, r=4.5, filled=True),
        line(9.0, 7.6, 9.0, 16.4, w=0.85, bg=True),
        line(9.0, 7.6, 15.6, 7.6, w=0.85, bg=True),
        line(9.0, 12.0, 14.4, 12.0, w=0.85, bg=True),
        line(9.0, 16.4, 15.6, 16.4, w=0.85, bg=True),
    ],
    # volume
    "volume": [
        _SPEAKER,
        arc(10.5, 7.5, 17.5, 16.5, -55, 55, w=0.85),
        arc(12.5, 4.5, 21.5, 19.5, -55, 55, w=0.85),
    ],
    "volume_up": [
        _SPEAKER_SMALL,
        arc(8.5, 8.0, 14.5, 16.0, -55, 55, w=0.8),
        line(18.5, 8.5, 18.5, 15.5, w=0.9),
        line(15.0, 12.0, 22.0, 12.0, w=0.9),
    ],
    "volume_down": [
        _SPEAKER_SMALL,
        arc(8.5, 8.0, 14.5, 16.0, -55, 55, w=0.8),
        line(15.0, 12.0, 22.0, 12.0, w=0.9),
    ],
    "muted": [
        _SPEAKER,
        line(14.8, 8.6, 20.6, 15.4, w=0.95),
        line(20.6, 8.6, 14.8, 15.4, w=0.95),
    ],
    "volume_stack": [
        _SPEAKER_SMALL,
        rect(13.0, 14.5, 15.2, 18.6, r=0.6),
        rect(16.4, 10.8, 18.6, 18.6, r=0.6),
        rect(19.8, 7.0, 22.0, 18.6, r=0.6),
    ],
    # composite / display
    "song_stack": [
        rect(3.5, 3.5, 20.5, 13.5, r=2.0, filled=False, w=0.9),
        dot(8.6, 10.2, 1.5),
        line(10.1, 10.2, 10.1, 6.4, w=0.6),
        poly([(10.1, 6.2), (14.2, 5.1), (14.2, 7.4), (10.1, 8.5)]),
        line(3.5, 16.6, 20.5, 16.6, w=0.85),
        line(3.5, 20.0, 14.0, 20.0, w=0.85),
    ],
    "clipboard": [
        rect(5.0, 4.6, 19.0, 20.6, r=2.4, filled=False, w=0.9),
        rect(9.0, 2.6, 15.0, 6.6, r=1.4, filled=True),
        line(8.6, 11.4, 15.4, 11.4, w=0.75),
        line(8.6, 15.4, 15.4, 15.4, w=0.75),
    ],
    "context": [
        line(3.5, 7.0, 16.0, 7.0, w=0.9),
        line(3.5, 12.0, 16.0, 12.0, w=0.9),
        line(3.5, 17.0, 11.0, 17.0, w=0.9),
        dot(17.3, 17.6, 2.2),
        line(19.5, 17.6, 19.5, 10.6, w=0.75),
    ],
    "device_transfer": [
        rect(2.5, 4.5, 11.0, 19.5, r=2.0, filled=False, w=0.9),
        line(4.8, 16.6, 8.7, 16.6, w=0.7),
        line(13.0, 12.0, 18.6, 12.0, w=0.9),
        poly([(17.6, 8.8), (22.0, 12.0), (17.6, 15.2)]),
    ],
    "user": [
        circle(12, 8.4, 4.3, filled=True),
        arc(4.4, 13.2, 19.6, 27.2, 180, 360, w=1.1),
    ],
    "play_context": [
        rect(3.0, 4.5, 21.0, 19.5, r=3.0, filled=False, w=0.9),
        poly([(9.8, 8.4), (16.6, 12.0), (9.8, 15.6)]),
    ],
    "add_to_playlist": [
        line(3.5, 7.0, 15.0, 7.0, w=0.9),
        line(3.5, 12.0, 15.0, 12.0, w=0.9),
        line(3.5, 17.0, 10.0, 17.0, w=0.9),
        line(17.6, 13.6, 17.6, 20.4, w=0.9),
        line(14.2, 17.0, 21.0, 17.0, w=0.9),
    ],
    "setup": [
        line(3.5, 7.5, 20.5, 7.5, w=0.9),
        dot(9.0, 7.5, 2.7),
        line(3.5, 16.5, 20.5, 16.5, w=0.9),
        dot(15.0, 16.5, 2.7),
    ],
    "music_note": list(_MUSIC_NOTE),
    # dials
    "playback_dial": [_RING, poly([(9.8, 7.6), (16.6, 12.0), (9.8, 16.4)])],
    "volume_dial": [
        _RING,
        poly([(7.0, 10.2), (9.4, 10.2), (12.4, 7.4), (12.4, 16.6), (9.4, 13.8), (7.0, 13.8)]),
        arc(11.4, 8.6, 16.4, 15.4, -55, 55, w=0.7),
    ],
    "playlist_dial": [
        _RING,
        line(7.6, 9.4, 16.4, 9.4, w=0.8),
        line(7.6, 12.4, 16.4, 12.4, w=0.8),
        line(7.6, 15.4, 13.0, 15.4, w=0.8),
    ],
    "liked_songs_dial": [
        _RING,
        dot(10.0, 15.4, 2.1),
        line(12.1, 15.4, 12.1, 8.4, w=0.75),
        poly([(12.1, 8.2), (16.4, 7.0), (16.4, 9.4), (12.1, 10.6)]),
    ],
    # A list with the playhead beside its first row: what is queued, in order.
    "queue_dial": [
        _RING,
        poly([(6.6, 7.4), (9.4, 9.3), (6.6, 11.2)]),
        line(11.0, 9.3, 17.0, 9.3, w=0.8),
        line(7.0, 13.2, 17.0, 13.2, w=0.8),
        line(7.0, 16.4, 13.6, 16.4, w=0.8),
    ],
    # A list with a note picked out of it: one song chosen from a collection.
    "song_picker_dial": [
        _RING,
        line(6.6, 8.6, 13.4, 8.6, w=0.8),
        line(6.6, 11.8, 11.4, 11.8, w=0.8),
        line(6.6, 15.0, 10.4, 15.0, w=0.8),
        dot(13.6, 16.0, 1.7),
        line(15.3, 16.0, 15.3, 10.2, w=0.7),
        poly([(15.3, 10.0), (18.4, 9.1), (18.4, 11.0), (15.3, 11.9)]),
    ],
    # status
    "no_device": [
        rect(3.0, 5.0, 21.0, 16.6, r=2.0, filled=False, w=0.9),
        line(8.0, 19.8, 16.0, 19.8, w=0.9),
        line(4.4, 19.6, 19.6, 4.4, w=1.0),
    ],
    "pending": [dot(6.0, 12.0, 1.9), dot(12.0, 12.0, 1.9), dot(18.0, 12.0, 1.9)],
    "busy": [arc(4.0, 4.0, 20.0, 20.0, 300, 190, w=1.1)],
    "rate_limited": [
        poly([(6.0, 3.6), (18.0, 3.6), (12.8, 12.0), (18.0, 20.4), (6.0, 20.4), (11.2, 12.0)], filled=False, w=0.9),
        line(6.0, 3.6, 18.0, 3.6, w=0.9),
        line(6.0, 20.4, 18.0, 20.4, w=0.9),
    ],
    "unavailable": [
        circle(12, 12, 8.6, filled=False, w=1.0),
        line(6.2, 17.8, 17.8, 6.2, w=1.0),
    ],
    "api_error": [
        poly([(12.0, 3.4), (21.6, 20.2), (2.4, 20.2)], filled=False, w=0.95),
        line(12.0, 9.2, 12.0, 14.8, w=0.95),
        dot(12.0, 17.6, 1.2),
    ],
    "auth_error": [
        rect(5.0, 10.4, 19.0, 20.6, r=2.0, filled=False, w=0.95),
        arc(8.0, 4.4, 16.0, 13.4, 180, 360, w=0.95),
        dot(12.0, 15.6, 1.4),
    ],
    "unknown": [
        arc(7.4, 4.4, 16.6, 13.6, 160, 20, w=1.0),
        line(12.0, 13.4, 12.0, 15.6, w=1.0),
        dot(12.0, 18.4, 1.3),
    ],
    "success": [polyline([(4.8, 12.4), (9.8, 17.4), (19.2, 6.8)], w=1.2)],
}

#: Which glyph stands in for each `ActionStatus` value.
STATUS_ICONS = {
    "ready": "unknown",
    "success": "success",
    "pending": "pending",
    "busy": "busy",
    "rate_limited": "rate_limited",
    "unavailable": "unavailable",
    "no_device": "no_device",
    "auth_required": "auth_error",
    "api_error": "api_error",
    "offline": "no_device",
    "unknown": "unknown",
}


def icon_names() -> list[str]:
    return sorted(ICONS)


# -- Pillow rendering -------------------------------------------------------


def _scaled(value: float, scale: float) -> float:
    return value * scale


def _draw_primitive(draw: ImageDraw.ImageDraw, primitive: dict, scale: float, color, bg_color) -> None:
    fill = bg_color if primitive.get("bg") else color
    stroke = max(1, int(round(ICON_STROKE * primitive.get("w", 1.0) * scale)))
    op = primitive["op"]

    if op == "line":
        points = [(_scaled(x, scale), _scaled(y, scale)) for x, y in primitive["points"]]
        draw.line(points, fill=fill, width=stroke, joint="curve")
        # Pillow has no round caps, so the ends are capped by hand. Without
        # this, thick strokes look chopped at small key sizes.
        radius = stroke / 2.0
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

    elif op == "poly":
        points = [(_scaled(x, scale), _scaled(y, scale)) for x, y in primitive["points"]]
        if primitive.get("filled", True):
            draw.polygon(points, fill=fill)
        else:
            draw.line(points + [points[0]], fill=fill, width=stroke, joint="curve")

    elif op == "rect":
        x0, y0, x1, y1 = (_scaled(value, scale) for value in primitive["box"])
        radius = _scaled(primitive.get("r", 0.0), scale)
        if primitive.get("filled", True):
            draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill)
        else:
            draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=fill, width=stroke)

    elif op == "circle":
        cx, cy = (_scaled(value, scale) for value in primitive["c"])
        radius = _scaled(primitive["r"], scale)
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        if primitive.get("filled", True):
            draw.ellipse(box, fill=fill)
        else:
            draw.ellipse(box, outline=fill, width=stroke)

    elif op == "arc":
        box = tuple(_scaled(value, scale) for value in primitive["box"])
        draw.arc(box, primitive["start"], primitive["end"], fill=fill, width=stroke)


@lru_cache(maxsize=256)
def render_icon(name: str, size: int, color: tuple, background: tuple | None = None) -> Image.Image:
    """An RGBA glyph, drawn oversized and reduced so the edges are smooth.

    Cached because the same handful of glyph/size/colour combinations are drawn
    on every state change, and a key press should not pay for rasterising.
    """
    primitives = ICONS.get(name)
    if primitives is None:
        primitives = ICONS["unknown"]

    size = max(8, int(size))
    working = size * SUPERSAMPLE
    scale = working / ICON_GRID

    # The knockout colour for `bg=True` parts, e.g. the tick inside a filled
    # circle. Transparent unless a background was given, so the glyph composites
    # correctly over artwork.
    bg_color = background + (255,) if background else (0, 0, 0, 0)
    fg_color = color + (255,) if len(color) == 3 else color

    image = Image.new("RGBA", (working, working), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    for primitive in primitives:
        _draw_primitive(draw, primitive, scale, fg_color, bg_color)

    return image.resize((size, size), Image.LANCZOS)


def paste_icon(
    target: Image.Image,
    name: str,
    size: int,
    center: tuple[int, int],
    color: tuple,
    background: tuple | None = None,
) -> None:
    glyph = render_icon(name, size, tuple(color), tuple(background) if background else None)
    target.paste(glyph, (int(center[0] - glyph.width / 2), int(center[1] - glyph.height / 2)), glyph)


# -- SVG output -------------------------------------------------------------


def _svg_color(color: tuple) -> str:
    return "#%02X%02X%02X" % tuple(color[:3])


def svg_for(name: str, color: tuple = (255, 255, 255), background: tuple | None = None) -> str:
    """The same glyph as a standalone SVG, for StreamController's action list."""
    primitives = ICONS.get(name) or ICONS["unknown"]
    fg = _svg_color(color)
    bg = _svg_color(background) if background else "none"

    parts: list[str] = []
    for primitive in primitives:
        paint = bg if primitive.get("bg") else fg
        width = ICON_STROKE * primitive.get("w", 1.0)
        op = primitive["op"]

        if op == "line":
            points = " ".join(f"{x},{y}" for x, y in primitive["points"])
            parts.append(
                f'<polyline points="{points}" fill="none" stroke="{paint}" '
                f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>'
            )
        elif op == "poly":
            points = " ".join(f"{x},{y}" for x, y in primitive["points"])
            if primitive.get("filled", True):
                parts.append(f'<polygon points="{points}" fill="{paint}"/>')
            else:
                parts.append(
                    f'<polygon points="{points}" fill="none" stroke="{paint}" '
                    f'stroke-width="{width}" stroke-linejoin="round"/>'
                )
        elif op == "rect":
            x0, y0, x1, y1 = primitive["box"]
            radius = primitive.get("r", 0.0)
            common = f'x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{radius}"'
            if primitive.get("filled", True):
                parts.append(f"<rect {common} fill=\"{paint}\"/>")
            else:
                parts.append(f"<rect {common} fill=\"none\" stroke=\"{paint}\" stroke-width=\"{width}\"/>")
        elif op == "circle":
            cx, cy = primitive["c"]
            radius = primitive["r"]
            if primitive.get("filled", True):
                parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{paint}"/>')
            else:
                parts.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
                    f'stroke="{paint}" stroke-width="{width}"/>'
                )
        elif op == "arc":
            parts.append(_svg_arc(primitive, paint, width))

    body = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(ICON_GRID)} {int(ICON_GRID)}" '
        f'width="{int(ICON_GRID)}" height="{int(ICON_GRID)}">{body}</svg>'
    )


def _svg_arc(primitive: dict, paint: str, width: float) -> str:
    x0, y0, x1, y1 = primitive["box"]
    rx, ry = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    cx, cy = x0 + rx, y0 + ry
    start, end = primitive["start"], primitive["end"]
    sweep = (end - start) % 360 or 360

    def point(angle_deg: float) -> tuple[float, float]:
        angle = math.radians(angle_deg)
        return (cx + rx * math.cos(angle), cy + ry * math.sin(angle))

    sx, sy = point(start)
    ex, ey = point(end)
    large = 1 if sweep > 180 else 0

    return (
        f'<path d="M {sx:.3f} {sy:.3f} A {rx} {ry} 0 {large} 1 {ex:.3f} {ey:.3f}" '
        f'fill="none" stroke="{paint}" stroke-width="{width}" stroke-linecap="round"/>'
    )
