"""Playback parsing must survive everything Spotify can put in `item`."""

from __future__ import annotations

from spotify_essentials.spotify.models import (
    ITEM_TYPE_EPISODE,
    ITEM_TYPE_TRACK,
    context_image_url,
    parse_devices,
    parse_playback,
    parse_playlists,
    parse_profile,
    parse_saved_tracks,
    parse_track,
    pick_image_url,
)

from .fakes import (
    device_payload,
    episode_payload,
    playback_payload,
    playlist_payload,
    track_payload,
)


def test_playing_track():
    state = parse_playback(playback_payload())

    assert state.has_playback
    assert state.is_playing
    assert state.track.name == "Blinding Lights"
    assert state.track.artists == ["The Weeknd"]
    assert state.duration_ms == 200040
    assert state.progress_ms == 42000
    assert state.device.name == "Desktop"
    assert state.volume_percent == 50
    assert state.context_type == "playlist"


def test_paused_track():
    state = parse_playback(playback_payload(is_playing=False))
    assert state.has_playback
    assert not state.is_playing


def test_no_playback_at_all():
    # Spotify answers 204 with no body when nothing is playing anywhere.
    state = parse_playback(None)
    assert not state.has_playback
    assert state.track is None
    assert state.device is None


def test_null_item():
    state = parse_playback(playback_payload(item=None))
    # An advert or a gap leaves the item empty; the device is still real.
    assert state.track is None
    assert state.device is not None


def test_episode_is_not_treated_as_a_track():
    state = parse_playback(playback_payload(item=episode_payload()))

    assert state.track.item_type == ITEM_TYPE_EPISODE
    assert state.track.album_name == "A Podcast"
    assert state.track.artists == ["Someone"]
    assert state.track.artwork_url == "https://i.example/show.jpg"


def test_unknown_future_type_still_parses():
    payload = track_payload()
    payload["type"] = "audiobook_chapter_from_2030"
    track = parse_track(payload)

    assert track is not None
    assert track.item_type == "audiobook_chapter_from_2030"
    assert track.name == "Blinding Lights"


def test_local_track_cannot_be_saved():
    track = parse_track(track_payload(is_local=True))
    assert track.is_local
    assert not track.supports_library


def test_track_without_id_cannot_be_saved():
    payload = track_payload()
    payload["id"] = None
    assert not parse_track(payload).supports_library


def test_multiple_artists_are_joined_in_order():
    track = parse_track(track_payload(artists=("Daft Punk", "Pharrell Williams")))
    assert track.artists == ["Daft Punk", "Pharrell Williams"]
    assert track.artist_text == "Daft Punk, Pharrell Williams"


def test_missing_artwork_is_none_not_an_error():
    track = parse_track(track_payload(with_artwork=False))
    assert track.artwork_url is None


def test_explicit_flag():
    assert parse_track(track_payload(explicit=True)).explicit is True
    assert parse_track(track_payload(explicit=False)).explicit is False


def test_disallowed_actions_are_collected():
    state = parse_playback(playback_payload(is_playing=True))
    assert not state.allows("resuming")
    assert state.allows("skipping_next")


def test_invalid_repeat_and_shuffle_become_unknown():
    payload = playback_payload()
    payload["repeat_state"] = "sideways"
    payload["shuffle_state"] = None
    state = parse_playback(payload)

    assert state.repeat_mode is None
    assert state.shuffle is None


def test_pick_image_url_prefers_the_closest_size():
    images = [
        {"url": "big", "width": 640},
        {"url": "mid", "width": 300},
        {"url": "small", "width": 64},
    ]
    assert pick_image_url(images, target_px=300) == "mid"
    assert pick_image_url(images, target_px=64) == "small"
    assert pick_image_url([]) is None
    assert pick_image_url([{"nope": 1}]) is None


def test_context_cover_comes_from_the_album_for_a_track():
    playlist = {"images": [{"url": "playlist.jpg", "width": 300}]}
    track = {"album": {"images": [{"url": "album.jpg", "width": 300}]}}

    assert context_image_url(playlist) == "playlist.jpg"
    assert context_image_url(track) == "album.jpg"
    assert context_image_url({}) is None
    assert context_image_url(None) is None


def test_device_parsing_defaults_supports_volume_to_true():
    payload = device_payload()
    del payload["supports_volume"]
    devices = parse_devices({"devices": [payload]})

    assert devices[0].supports_volume is True


def test_device_without_volume_support():
    devices = parse_devices({"devices": [device_payload(supports_volume=False, volume=None)]})
    assert devices[0].supports_volume is False
    assert devices[0].volume_percent is None


def test_restricted_device():
    devices = parse_devices({"devices": [device_payload(is_restricted=True)]})
    assert devices[0].is_restricted


def test_saved_tracks_unwrap_their_container():
    payload = {"items": [{"added_at": "x", "track": track_payload()}, {"added_at": "y", "track": None}]}
    tracks = parse_saved_tracks(payload)

    assert len(tracks) == 1
    assert tracks[0].item_type == ITEM_TYPE_TRACK


def test_playlists_skip_entries_without_an_id():
    payload = {"items": [playlist_payload(1), {"name": "broken"}]}
    playlists = parse_playlists(payload)

    assert len(playlists) == 1
    assert playlists[0].track_count == 11


def test_profile():
    profile = parse_profile(
        {"id": "abc", "display_name": "Someone", "product": "premium", "images": [{"url": "u", "width": 160}]}
    )
    assert profile.account_id == "abc"
    assert profile.is_premium is True

    free = parse_profile({"id": "abc", "product": "free"})
    assert free.is_premium is False
    assert free.display_name == "abc"

    unknown = parse_profile({"id": "abc"})
    assert unknown.is_premium is None

    assert parse_profile({}) is None
