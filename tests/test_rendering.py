"""Renderers, including the rules Spotify sets for displaying artwork.

Nothing here needs a deck or a network: every renderer is a function from state
to a Pillow image, which is what makes "is the artwork cropped?" an assertion
rather than an eyeball check.
"""

from __future__ import annotations

from PIL import Image

from spotify_essentials.rendering import theme
from spotify_essentials.rendering.dial import (
    render_browse_dial,
    render_playback_dial,
    render_volume_dial,
)
from spotify_essentials.rendering.icons import ICONS, icon_names, render_icon, svg_for
from spotify_essentials.rendering.key import (
    render_glyph_key,
    render_mode_stack_key,
    render_text_key,
    render_value_key,
)
from spotify_essentials.rendering.song import render_song_stack
from spotify_essentials.rendering.status import STATUS_LABELS, render_status, render_success_overlay
from spotify_essentials.spotify.state import ActionStatus

KEY = (144, 144)
SMALL_KEY = (72, 72)
DIAL = (200, 100)

ARTWORK_COLOR = (255, 0, 0)
LONG_TITLE = "A Song Title Far Too Long For Any Stream Deck Key To Show At Once"


def artwork(size=(300, 300)) -> Image.Image:
    return Image.new("RGB", size, ARTWORK_COLOR)


def colour_bounds(image: Image.Image, colour=ARTWORK_COLOR):
    """Bounding box and pixel count of an exact colour."""
    pixels = image.convert("RGB").load()
    width, height = image.size
    found = []
    for y in range(height):
        for x in range(width):
            if pixels[x, y] == colour:
                found.append((x, y))
    if not found:
        return None, 0
    xs = [point[0] for point in found]
    ys = [point[1] for point in found]
    return (min(xs), min(ys), max(xs), max(ys)), len(found)


def is_blank(image: Image.Image) -> bool:
    return len(image.convert("RGB").getcolors(maxcolors=100000) or []) <= 1


# -- icons -----------------------------------------------------------------


def test_every_required_icon_exists():
    required = {
        "setup", "play", "pause", "previous", "next", "seek_backward", "seek_forward",
        "shuffle", "repeat_context", "repeat_track", "mode_stack",
        "library_add", "library_saved", "explicit",
        "volume_up", "volume_down", "volume", "muted", "volume_stack",
        "song_stack", "clipboard", "context", "device_transfer", "user",
        "play_context", "add_to_playlist",
        "playback_dial", "volume_dial", "playlist_dial", "liked_songs_dial",
        "no_device", "pending", "busy", "rate_limited", "unavailable",
        "api_error", "auth_error", "unknown", "success", "music_note",
    }
    assert required <= set(icon_names())


def test_icons_render_visibly_at_deck_sizes():
    for name in icon_names():
        for size in (24, 44, 96):
            glyph = render_icon(name, size, theme.SPOTIFY_GREEN)
            assert glyph.size == (size, size)
            # Every glyph must actually put ink on the key.
            alpha = glyph.getchannel("A")
            assert alpha.getextrema()[1] > 0, f"{name} at {size}px drew nothing"


def test_icon_geometry_stays_on_the_grid():
    for name, primitives in ICONS.items():
        for primitive in primitives:
            points = list(primitive.get("points", []))
            if "box" in primitive:
                x0, y0, x1, y1 = primitive["box"]
                points += [(x0, y0), (x1, y1)]
            if "c" in primitive:
                cx, cy = primitive["c"]
                radius = primitive["r"]
                points += [(cx - radius, cy - radius), (cx + radius, cy + radius)]
            for x, y in points:
                assert -1 <= x <= 25, f"{name} strays off the 24-unit grid at x={x}"
                assert -1 <= y <= 28, f"{name} strays off the 24-unit grid at y={y}"


def test_svg_output_matches_the_same_geometry():
    svg = svg_for("play", theme.SPOTIFY_GREEN)
    assert svg.startswith("<svg")
    assert 'viewBox="0 0 24 24"' in svg
    assert "#1ED760" in svg
    # Every icon must produce a parseable, non-empty document.
    for name in icon_names():
        body = svg_for(name)
        assert body.endswith("</svg>") and len(body) > 60


# -- keys ------------------------------------------------------------------


def test_glyph_key_states():
    for kwargs in (
        {"color": theme.SPOTIFY_GREEN},
        {"color": theme.WHITE, "caption": "OFF", "dim": True},
        {"color": theme.SPOTIFY_GREEN, "caption": "ON", "active": True},
        {"color": theme.SPOTIFY_GREEN, "active": True},
    ):
        image = render_glyph_key(KEY, "shuffle", **kwargs)
        assert image.size == KEY
        assert not is_blank(image)


def test_value_key_shows_the_level():
    image = render_value_key(KEY, "volume", "65%", fraction=0.65)
    assert image.size == KEY
    assert not is_blank(image)


def test_mode_stack_shows_all_three_modes():
    known = render_mode_stack_key(KEY, shuffle=True, repeat_mode="context")
    unknown = render_mode_stack_key(KEY, shuffle=None, repeat_mode=None)

    assert known.size == KEY
    # An unknown state must not look identical to a known-off state.
    off = render_mode_stack_key(KEY, shuffle=False, repeat_mode="off")
    assert unknown.tobytes() != off.tobytes()
    assert known.tobytes() != off.tobytes()


def test_renderers_fit_a_small_key_too():
    for image in (
        render_glyph_key(SMALL_KEY, "play", caption="PLAY"),
        render_value_key(SMALL_KEY, "volume", "100%", fraction=1.0),
        render_mode_stack_key(SMALL_KEY, shuffle=True, repeat_mode="track"),
        render_text_key(SMALL_KEY, ["Discover Weekly"], accent="PLAYLIST"),
    ):
        assert image.size == SMALL_KEY
        assert not is_blank(image)


# -- status ----------------------------------------------------------------


def test_every_status_renders_on_keys_and_dials():
    for status in ActionStatus:
        key_image = render_status(status, size=KEY, title="Spotify")
        dial_image = render_status(status, is_dial=True, size=DIAL, title="Spotify")

        assert key_image.size == KEY
        assert dial_image.size == DIAL
        assert not is_blank(key_image)
        assert not is_blank(dial_image)


def test_status_labels_are_short_enough_to_read():
    for label in STATUS_LABELS.values():
        for line in label.split("\n"):
            assert len(line) <= 8, f"{line!r} will not read at key size"


def test_success_overlay():
    image = render_success_overlay(KEY, "ADDED")
    assert image.size == KEY
    assert not is_blank(image)


def test_success_overlay_can_be_composited_the_way_the_deck_does_it():
    # StreamController pastes an overlay using the overlay itself as the
    # transparency mask. Pillow raises "bad transparency mask" for an RGB
    # image, which wedges every later redraw of that key.
    overlay = render_success_overlay(KEY, "LIKED")
    assert overlay.mode == "RGBA"

    background = Image.new("RGBA", KEY, (0, 0, 0, 0))
    scaled = overlay.resize((KEY[0] * 3 // 4, KEY[1] * 3 // 4))
    background.paste(scaled, (KEY[0] // 8, KEY[1] // 8), scaled)

    # Opaque, so the overlay actually hides the key art behind it.
    assert scaled.getchannel("A").getextrema() == (255, 255)


# -- Song Stack ------------------------------------------------------------


def test_song_stack_shows_everything_it_is_asked_to():
    result = render_song_stack(
        KEY,
        track_name="Blinding Lights",
        artist="The Weeknd",
        artwork=artwork(),
        is_playing=True,
        fraction=0.42,
        like_state="liked",
        explicit=True,
    )
    assert result.image.size == KEY
    assert not is_blank(result.image)


def test_artwork_is_never_cropped_or_stretched():
    # A 2:1 image must stay 2:1 and must appear whole.
    result = render_song_stack(
        KEY,
        track_name="Song",
        artist="Artist",
        artwork=Image.new("RGB", (300, 150), ARTWORK_COLOR),
        is_playing=True,
        fraction=0.5,
    )
    bounds, count = colour_bounds(result.image)

    assert bounds is not None, "the artwork should be visible"
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0 + 1, y1 - y0 + 1
    assert abs(width / height - 2.0) < 0.1, f"aspect ratio changed: {width}x{height}"
    # Every pixel of the pasted rectangle is still the artwork colour, so
    # nothing was drawn on top of it.
    assert count == width * height, "something is overlapping the album art"


def test_tall_artwork_is_letterboxed_not_cropped():
    result = render_song_stack(
        KEY,
        track_name="Song",
        artist="Artist",
        artwork=Image.new("RGB", (150, 300), ARTWORK_COLOR),
        show_progress=False,
    )
    bounds, count = colour_bounds(result.image)
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0 + 1, y1 - y0 + 1

    assert abs(width / height - 0.5) < 0.1
    assert count == width * height


def test_song_stack_without_artwork_uses_a_placeholder():
    result = render_song_stack(KEY, track_name="Song", artist="Artist", artwork=None)
    assert not is_blank(result.image)


def test_long_text_reports_its_overflow_for_the_marquee():
    result = render_song_stack(KEY, track_name=LONG_TITLE, artist="Someone With A Very Long Name Indeed")

    assert result.title_overflow > 0
    assert result.artist_overflow > 0

    short = render_song_stack(KEY, track_name="Hey", artist="You")
    assert short.title_overflow == 0
    assert short.artist_overflow == 0


def test_marquee_offset_moves_the_text():
    first = render_song_stack(KEY, track_name=LONG_TITLE, artist="A", title_offset=0)
    later = render_song_stack(KEY, track_name=LONG_TITLE, artist="A", title_offset=30)
    assert first.image.tobytes() != later.image.tobytes()


def test_each_section_can_be_switched_off():
    minimal = render_song_stack(
        KEY,
        track_name="Song",
        artist="Artist",
        artwork=artwork(),
        show_artwork=False,
        show_title=False,
        show_artist=False,
        show_progress=False,
        show_like_state=False,
        show_explicit=False,
    )
    assert minimal.image.size == KEY
    assert not is_blank(minimal.image), "an all-off Song Stack should still say what it does"

    # Switching artwork off must not leave a hole where it was.
    without = render_song_stack(KEY, track_name="Song", artist="Artist", show_artwork=False)
    with_art = render_song_stack(KEY, track_name="Song", artist="Artist", artwork=artwork())
    assert without.image.tobytes() != with_art.image.tobytes()


def test_all_the_states_that_have_to_be_drawable():
    states = [
        {"is_playing": True},
        {"is_playing": False},
        {"like_state": "liked"},
        {"like_state": "not_liked"},
        {"like_state": "unknown"},
        {"like_state": "busy"},
        {"explicit": True},
        {"track_name": LONG_TITLE},
        {"artist": LONG_TITLE},
        {"artwork": None},
        {"fraction": None},
        {"fraction": 0.0},
        {"fraction": 1.0},
    ]
    for overrides in states:
        payload = {"track_name": "Song", "artist": "Artist", "artwork": artwork(), "fraction": 0.3}
        payload.update(overrides)
        result = render_song_stack(KEY, **payload)
        assert result.image.size == KEY


# -- dials -----------------------------------------------------------------


def test_playback_dial_layouts():
    with_art = render_playback_dial(
        DIAL,
        track_name="Blinding Lights",
        artist="The Weeknd",
        is_playing=True,
        fraction=0.4,
        position_text="1:42",
        duration_text="3:20",
        artwork=artwork(),
    )
    assert with_art.image.size == DIAL
    assert not is_blank(with_art.image)

    seeking = render_playback_dial(
        DIAL,
        track_name="Blinding Lights",
        artist="The Weeknd",
        is_playing=True,
        fraction=0.4,
        position_text="1:47",
        duration_text="3:20",
        seek_hint="SEEK +5s",
    )
    # The seek readout has to be visibly different from the normal line.
    normal = render_playback_dial(
        DIAL, track_name="Blinding Lights", artist="The Weeknd", is_playing=True, fraction=0.4
    )
    assert seeking.image.tobytes() != normal.image.tobytes()


def test_dial_artwork_is_not_cropped_either():
    result = render_playback_dial(
        DIAL,
        track_name="Song",
        artist="Artist",
        is_playing=True,
        fraction=0.1,
        artwork=Image.new("RGB", (300, 150), ARTWORK_COLOR),
    )
    bounds, count = colour_bounds(result.image)
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0 + 1, y1 - y0 + 1

    assert abs(width / height - 2.0) < 0.15
    assert count == width * height


def test_volume_dial_states():
    normal = render_volume_dial(DIAL, percent=65)
    muted = render_volume_dial(DIAL, percent=0, muted=True)
    unsupported = render_volume_dial(DIAL, percent=None, supports_volume=False)
    unknown = render_volume_dial(DIAL, percent=None)

    for image in (normal, muted, unsupported, unknown):
        assert image.size == DIAL
        assert not is_blank(image)

    assert normal.tobytes() != muted.tobytes()
    assert unsupported.tobytes() != normal.tobytes()


def test_browse_dial_states():
    result = render_browse_dial(
        DIAL, title="Liked Mix", subtitle="47 songs", position_text="12 / 47", artwork=artwork()
    )
    assert result.image.size == DIAL

    without_art = render_browse_dial(DIAL, title="Everlong", subtitle="Foo Fighters", position_text="143 / 1268")
    assert not is_blank(without_art.image)

    long_name = render_browse_dial(DIAL, title=LONG_TITLE, position_text="1 / 2")
    assert long_name.title_overflow > 0
