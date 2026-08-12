"""Link parsing and clipboard templating."""

from __future__ import annotations

from spotify_essentials.spotify.format import (
    FORMAT_CUSTOM,
    FORMAT_TRACK_ARTIST,
    FORMAT_TRACK_ARTIST_URL,
    FORMAT_URL,
    format_song,
    render_template,
)
from spotify_essentials.spotify.models import parse_track
from spotify_essentials.spotify.uri import (
    external_url_for_uri,
    make_resource,
    parse_id,
    parse_resource,
    uri_type,
)

from .fakes import track_payload

TRACK_ID = "4cOdK2wGLETKBW3PvgPWqT"


def test_plain_uris():
    for kind in ("track", "album", "artist", "playlist"):
        resource = parse_resource(f"spotify:{kind}:{TRACK_ID}")
        assert resource.resource_type == kind
        assert resource.resource_id == TRACK_ID
        assert resource.uri == f"spotify:{kind}:{TRACK_ID}"


def test_share_urls_drop_tracking_parameters():
    resource = parse_resource(f"https://open.spotify.com/album/{TRACK_ID}?si=abcdef123456&utm_source=copy-link")
    assert resource.uri == f"spotify:album:{TRACK_ID}"


def test_locale_prefixed_urls():
    resource = parse_resource(f"https://open.spotify.com/intl-de/track/{TRACK_ID}")
    assert resource.resource_type == "track"


def test_url_without_a_scheme():
    assert parse_resource(f"open.spotify.com/playlist/{TRACK_ID}").resource_type == "playlist"


def test_legacy_user_playlist_forms():
    assert parse_resource(f"spotify:user:bob:playlist:{TRACK_ID}").resource_type == "playlist"
    assert parse_resource(f"https://open.spotify.com/user/bob/playlist/{TRACK_ID}").resource_type == "playlist"


def test_rejects_junk_before_any_request_is_made():
    for value in ("", "   ", "not a link", "https://example.com/track/abc", "spotify:track:", "spotify:track:has space"):
        assert parse_resource(value) is None


def test_bare_ids_need_a_declared_type():
    assert parse_resource(TRACK_ID) is None
    assert parse_id("track", TRACK_ID).uri == f"spotify:track:{TRACK_ID}"
    assert parse_id("track", f"spotify:album:{TRACK_ID}") is None


def test_context_and_item_classification():
    # Albums, artists and playlists are contexts; a track has to be played
    # through the uris list instead.
    assert parse_resource(f"spotify:album:{TRACK_ID}").is_context
    assert not parse_resource(f"spotify:album:{TRACK_ID}").is_item
    assert parse_resource(f"spotify:track:{TRACK_ID}").is_item
    assert not parse_resource(f"spotify:track:{TRACK_ID}").is_context


def test_external_url_round_trip():
    assert external_url_for_uri(f"spotify:track:{TRACK_ID}") == f"https://open.spotify.com/track/{TRACK_ID}"
    assert external_url_for_uri("rubbish") is None


def test_uri_type_and_make_resource():
    assert uri_type(f"spotify:show:{TRACK_ID}") == "show"
    assert uri_type("nonsense") is None
    assert make_resource("track", "bad id!") is None
    assert make_resource("nope", TRACK_ID) is None


# -- clipboard formats -----------------------------------------------------


def _track():
    return parse_track(track_payload(artists=("Daft Punk", "Pharrell Williams")))


def test_preset_formats():
    track = _track()
    assert format_song(FORMAT_TRACK_ARTIST, track) == "Blinding Lights — Daft Punk"
    assert format_song(FORMAT_URL, track) == f"https://open.spotify.com/track/{TRACK_ID}"
    assert format_song(FORMAT_TRACK_ARTIST_URL, track).splitlines() == [
        "Blinding Lights — Daft Punk",
        f"https://open.spotify.com/track/{TRACK_ID}",
    ]


def test_all_artists_placeholder():
    assert render_template("{artists}", _track()) == "Daft Punk, Pharrell Williams"


def test_custom_template_with_every_placeholder():
    text = format_song(FORMAT_CUSTOM, _track(), custom_template="{track}|{artist}|{album}|{uri}")
    assert text == f"Blinding Lights|Daft Punk|After Hours|spotify:track:{TRACK_ID}"


def test_unknown_placeholder_is_left_alone():
    assert render_template("{track} {nope}", _track()) == "Blinding Lights {nope}"


def test_formatting_without_a_track_is_empty_not_an_error():
    assert format_song(FORMAT_TRACK_ARTIST, None) == "—"
    assert format_song(FORMAT_URL, None) == ""
