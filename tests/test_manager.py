"""The manager, driven against the fake API.

The worker threads are not started here: the queue is drained by hand so each
test can say exactly what happened, in what order, and how many requests it
took. That is the only way to assert things like "a fast dial spin sends two
requests, not forty".
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from spotify_essentials.spotify.api import Endpoints
from spotify_essentials.spotify.auth import TokenSet, TokenStore
from spotify_essentials.spotify.errors import (
    SpotifyApiError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyRateLimitError,
    SpotifyRestrictedError,
)
from spotify_essentials.spotify.manager import SpotifyManager
from spotify_essentials.spotify.state import ActionStatus, LikeState

from .fakes import (
    FakeSpotifyApi,
    device_payload,
    episode_payload,
    playback_payload,
    playlist_payload,
    saved_track_payloads,
    track_payload,
)

PLAYLIST_PATH = "/me/playlists"
VOLUME_PATH = "/me/player/volume"
SEEK_PATH = "/me/player/seek"
LIBRARY_PATH = "/me/library"
CONTAINS_PATH = "/me/library/contains"
#: Liked Songs as a browsable context, which is all it is to a picker now.
LIKED = "spotify:collection:tracks"


@pytest.fixture
def manager(tmp_path):
    """A manager with a fake API, no threads, and a valid-looking session."""
    api = FakeSpotifyApi()
    token_path = tmp_path / "spotify-auth.json"
    TokenStore(str(token_path)).save(TokenSet("access", "refresh", time.time() + 3600))

    instance = SpotifyManager(
        token_path=str(token_path),
        settings_provider=lambda: {"spotify_client_id": "abc", "playback_poll_interval_ms": 1000},
        api=api,
        auto_start=False,
    )
    instance.command_settle_seconds = 0
    instance.api = api
    yield instance
    instance.shutdown()


def drain(manager) -> int:
    """Run everything queued, through the same error handling the worker uses."""
    performed = 0
    for _ in range(200):
        work = manager._queue.take(timeout=0)
        if work is None:
            break
        manager.run_command(work)
        performed += 1
    return performed


def poll(manager) -> None:
    manager._poll_playback()


def settle(manager) -> None:
    """Poll once and finish the follow-up work a poll queues.

    A track appearing for the first time triggers a liked-state check and a
    context lookup; tests that count later requests start from here.
    """
    poll(manager)
    drain(manager)


# -- polling ---------------------------------------------------------------


def test_polling_populates_state(manager):
    poll(manager)
    state = manager.get_playback_state()

    assert state.is_playing
    assert state.track.name == "Blinding Lights"
    assert manager.get_status() is ActionStatus.READY


def test_listeners_are_notified_only_on_a_visible_change(manager):
    calls = []
    manager.add_listener(lambda: calls.append(1), {"playback"})

    poll(manager)
    assert len(calls) == 1

    # Progress moves every second, but nothing drawn from state changed.
    manager.api.playback["progress_ms"] += 1000
    poll(manager)
    assert len(calls) == 1

    manager.api.playback["is_playing"] = False
    poll(manager)
    assert len(calls) == 2


def test_a_mode_spotify_never_applied_stops_being_shown(manager):
    # The Mode Stack bug: the key shows the mode the press asked for, Spotify
    # never applies it, and nothing Spotify reports changes — so a comparison
    # against the last poll sees nothing and the key keeps drawing a lie.
    manager.api.playback["repeat_state"] = "context"
    poll(manager)

    shown = []
    manager.add_listener(lambda: shown.append(manager.get_playback_state().repeat_mode), {"playback"})

    manager.set_repeat("track")
    manager._queue.take(timeout=0)  # drop the command: Spotify never hears it
    assert manager.get_playback_state().repeat_mode == "track", "the press should show at once"
    assert shown == ["track"]

    manager._optimistic["repeat_mode"] = ("track", time.monotonic() - 0.01)
    poll(manager)

    assert manager.get_playback_state().repeat_mode == "context"
    assert shown[-1] == "context", "the key was never told the mode went back"


def test_a_mode_change_made_elsewhere_reaches_the_key(manager):
    # Changed in the Spotify app rather than on the deck.
    poll(manager)
    shown = []
    manager.add_listener(lambda: shown.append(manager.get_playback_state()), {"playback"})

    manager.api.playback["repeat_state"] = "track"
    manager.api.playback["shuffle_state"] = True
    poll(manager)

    assert [(state.repeat_mode, state.shuffle) for state in shown] == [("track", True)]


def test_a_confirmed_mode_does_not_redraw_twice(manager):
    # settle() first: a first poll also queues the context and liked lookups,
    # and those notify on their own account.
    settle(manager)
    calls = []
    manager.add_listener(lambda: calls.append(1), {"playback"})

    manager.set_repeat("track")  # one notify: the optimistic value
    drain(manager)               # the fake applies it
    poll(manager)                # Spotify now agrees, so nothing new to draw

    assert len(calls) == 1


def test_removed_listeners_stop_being_called(manager):
    calls = []

    def listener():
        calls.append(1)

    manager.add_listener(listener, {"playback"})
    poll(manager)
    manager.remove_listener(listener)

    manager.api.playback["is_playing"] = False
    poll(manager)
    assert len(calls) == 1


def test_a_failing_listener_does_not_stop_the_others(manager):
    calls = []
    manager.add_listener(lambda: (_ for _ in ()).throw(RuntimeError("boom")), {"playback"})
    manager.add_listener(lambda: calls.append(1), {"playback"})

    poll(manager)
    assert calls == [1]


def test_external_changes_are_picked_up_without_any_input(manager):
    poll(manager)
    manager.api.playback["shuffle_state"] = True
    manager.api.playback["repeat_state"] = "track"
    poll(manager)

    state = manager.get_playback_state()
    assert state.shuffle is True
    assert state.repeat_mode == "track"


def test_smart_shuffle_turned_on_elsewhere_reaches_the_key(manager):
    # Smart shuffle can only be switched on in Spotify itself, so arriving from
    # outside is the only way a key ever sees it.
    poll(manager)
    shown = []
    manager.add_listener(lambda: shown.append(manager.get_playback_state()), {"playback"})

    manager.api.playback["shuffle_state"] = True
    manager.api.playback["smart_shuffle"] = True
    poll(manager)

    assert [state.is_smart_shuffle for state in shown] == [True]


def test_smart_shuffle_switched_off_elsewhere_also_redraws(manager):
    manager.api.playback["shuffle_state"] = True
    manager.api.playback["smart_shuffle"] = True
    poll(manager)
    shown = []
    manager.add_listener(lambda: shown.append(manager.get_playback_state()), {"playback"})

    # Back to ordinary shuffle: `shuffle_state` never moved, so only the smart
    # field distinguishes the two pictures.
    manager.api.playback["smart_shuffle"] = False
    poll(manager)

    assert [state.is_smart_shuffle for state in shown] == [False]


def test_pressing_shuffle_while_smart_turns_shuffle_off(manager):
    manager.api.playback["shuffle_state"] = True
    manager.api.playback["smart_shuffle"] = True
    poll(manager)

    manager.toggle_shuffle()
    drain(manager)

    assert manager.api.calls_to("/me/player/shuffle")[-1].detail["state"] is False


def test_turning_shuffle_off_stops_showing_smart_at_once(manager):
    manager.api.playback["shuffle_state"] = True
    manager.api.playback["smart_shuffle"] = True
    poll(manager)
    assert manager.get_playback_state().is_smart_shuffle

    manager.set_shuffle(False)
    # Nothing has been sent yet, and the key must not still be claiming SMART.
    assert manager.api.calls_to("/me/player/shuffle") == []
    assert manager.get_playback_state().is_smart_shuffle is False


def test_a_refused_shuffle_off_goes_back_to_showing_smart(manager):
    # Spotify decides for itself what happens to smart shuffle when shuffle is
    # switched off; if it keeps it, the key has to admit that.
    manager.api.playback["shuffle_state"] = True
    manager.api.playback["smart_shuffle"] = True
    poll(manager)

    manager.set_shuffle(False)
    manager._optimistic["shuffle"] = (False, time.monotonic() - 0.01)
    manager.api.playback["shuffle_state"] = True
    poll(manager)

    assert manager.get_playback_state().is_smart_shuffle is True


# -- optimistic state ------------------------------------------------------


def test_play_pause_updates_the_display_before_the_request(manager):
    poll(manager)
    assert manager.get_playback_state().is_playing

    manager.pause()
    # Nothing has been sent yet, but the deck already shows the new state.
    assert manager.api.calls_to("/me/player/pause") == []
    assert not manager.get_playback_state().is_playing

    drain(manager)
    assert len(manager.api.calls_to("/me/player/pause")) == 1


def test_optimistic_state_is_dropped_once_spotify_agrees(manager):
    poll(manager)
    manager.pause()
    drain(manager)
    poll(manager)

    assert manager._optimistic == {}
    assert not manager.get_playback_state().is_playing


def test_optimistic_state_expires_if_spotify_never_agrees(manager):
    poll(manager)
    manager._set_optimistic("is_playing", False)
    # Pretend the command was sent three seconds ago and ignored.
    manager._optimistic["is_playing"] = (False, time.monotonic() - 0.01)
    poll(manager)

    assert manager.get_playback_state().is_playing


# -- volume ----------------------------------------------------------------


def test_volume_steps_are_clamped(manager):
    poll(manager)

    manager.set_volume(140)
    assert manager.get_volume() == 100
    manager.set_volume(-5)
    assert manager.get_volume() == 0


def test_rapid_dial_turns_coalesce_but_send_the_final_value(manager):
    settle(manager)
    for _ in range(5):
        manager.adjust_volume(5)

    # Five turns, one queued request.
    assert manager._queue.pending() == 1
    assert manager.get_volume() == 75

    drain(manager)
    volume_calls = manager.api.calls_to(VOLUME_PATH)
    assert len(volume_calls) == 1
    assert volume_calls[0].detail["volume_percent"] == 75


def test_a_turn_after_the_request_went_out_sends_a_second_request(manager):
    poll(manager)
    manager.adjust_volume(5)
    drain(manager)
    manager.adjust_volume(5)
    drain(manager)

    levels = [call.detail["volume_percent"] for call in manager.api.calls_to(VOLUME_PATH)]
    assert levels == [55, 60]


def test_volume_changes_do_not_coalesce_with_transport_commands(manager):
    settle(manager)
    manager.pause()
    manager.adjust_volume(5)
    manager.next_track()

    assert manager._queue.pending() == 3
    drain(manager)
    assert manager.api.paths()[-3:] == ["/me/player/pause", VOLUME_PATH, "/me/player/next"]


def test_mute_remembers_the_level_and_restores_it(manager):
    poll(manager)
    manager.set_volume(70)
    drain(manager)
    poll(manager)

    manager.toggle_mute()
    drain(manager)
    assert manager.get_volume() == 0

    poll(manager)
    manager.toggle_mute()
    drain(manager)
    assert manager.get_volume() == 70


def test_unmuting_from_an_unknown_level_falls_back(manager):
    manager.api.playback = playback_payload(device=device_payload(volume=0))
    poll(manager)

    manager.toggle_mute()
    drain(manager)
    assert manager.get_volume() == 50


def test_hold_mute_restores_on_release(manager):
    poll(manager)
    restore = manager.begin_hold_mute()
    drain(manager)

    assert restore == 50
    assert manager.get_volume() == 0

    manager.end_hold_mute(restore, device_at_press="device-1")
    drain(manager)
    assert manager.get_volume() == 50


def test_hold_mute_does_not_restore_if_the_device_changed(manager):
    poll(manager)
    restore = manager.begin_hold_mute()
    drain(manager)

    # Playback moved to the phone while the dial was held.
    manager.api.playback = playback_payload(device=device_payload(device_id="phone", name="Phone", volume=20))
    poll(manager)

    manager.end_hold_mute(restore, device_at_press="device-1")
    drain(manager)

    levels = [call.detail["volume_percent"] for call in manager.api.calls_to(VOLUME_PATH)]
    assert levels == [0], "restoring onto a different device would blast the wrong speaker"


# -- seeking ---------------------------------------------------------------


def test_seeking_uses_the_interpolated_position(manager):
    poll(manager)
    manager.seek_relative(5000)
    drain(manager)

    call = manager.api.calls_to(SEEK_PATH)[0]
    assert 47000 <= call.detail["position_ms"] <= 47500


def test_rapid_seeks_coalesce_to_the_final_position(manager):
    settle(manager)
    for _ in range(4):
        manager.seek_relative(5000)

    assert manager._queue.pending() == 1
    drain(manager)

    calls = manager.api.calls_to(SEEK_PATH)
    assert len(calls) == 1
    assert calls[0].detail["position_ms"] >= 62000


def test_seeking_never_runs_past_the_end(manager):
    manager.api.playback = playback_payload(progress_ms=199000, is_playing=False)
    poll(manager)
    manager.seek_relative(60000)
    drain(manager)

    assert manager.api.calls_to(SEEK_PATH)[0].detail["position_ms"] == 199040


def test_seeking_never_goes_below_zero(manager):
    manager.api.playback = playback_payload(progress_ms=2000, is_playing=False)
    poll(manager)
    manager.seek_relative(-10000)
    drain(manager)

    assert manager.api.calls_to(SEEK_PATH)[0].detail["position_ms"] == 0


# -- library ---------------------------------------------------------------


def test_like_state_is_fetched_when_the_track_changes(manager):
    manager.api.liked.add("spotify:track:4cOdK2wGLETKBW3PvgPWqT")
    poll(manager)
    drain(manager)

    assert manager.get_like_state() is LikeState.LIKED
    assert len(manager.api.calls_to(CONTAINS_PATH)) == 1

    # Polling again with the same track must not re-check.
    poll(manager)
    drain(manager)
    assert len(manager.api.calls_to(CONTAINS_PATH)) == 1

    manager.api.playback["item"] = track_payload(track_id="0000000000000000000000", name="Another")
    poll(manager)
    drain(manager)
    assert len(manager.api.calls_to(CONTAINS_PATH)) == 2


def test_toggling_like_uses_the_generic_library_endpoints(manager):
    poll(manager)
    drain(manager)

    results = []
    manager.toggle_like(on_result=results.append)
    drain(manager)

    assert results == [True]
    assert manager.api.calls_to(LIBRARY_PATH)[0].method == "PUT"
    assert manager.get_like_state() is LikeState.LIKED

    manager.toggle_like(on_result=results.append)
    drain(manager)
    assert manager.api.calls_to(LIBRARY_PATH)[1].method == "DELETE"
    assert manager.get_like_state() is LikeState.NOT_LIKED


def test_like_is_refused_for_items_that_cannot_be_saved(manager):
    manager.api.playback = playback_payload(item=episode_payload())
    poll(manager)
    drain(manager)

    manager.toggle_like()
    drain(manager)

    assert manager.api.calls_to(LIBRARY_PATH) == []
    assert manager.get_like_state() is LikeState.UNKNOWN


def test_adding_to_a_playlist_does_not_check_for_duplicates_first(manager):
    poll(manager)
    results = []
    manager.add_current_to_playlist("pl1", on_result=results.append)
    drain(manager)

    assert results == [True]
    assert manager.api.paths()[-1] == "/playlists/pl1/items"


# -- playlists and liked songs ---------------------------------------------


def test_playlists_load_in_the_background_and_page(manager):
    manager.api.playlists = [playlist_payload(index) for index in range(120)]

    assert manager.get_playlists() is None  # first call starts the load
    drain(manager)

    playlists = manager.get_playlists()
    assert len(playlists) == 120
    assert len(manager.api.calls_to(PLAYLIST_PATH)) == 3


def test_refreshing_playlists_reloads_them(manager):
    manager.api.playlists = [playlist_payload(0)]
    manager.get_playlists()
    drain(manager)

    manager.api.playlists = [playlist_payload(0), playlist_payload(1)]
    manager.refresh_playlists()
    drain(manager)

    assert len(manager.get_playlists()) == 2


def test_liked_songs_load_one_page_at_a_time(manager):
    manager.api.saved_tracks = saved_track_payloads(5000)

    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)

    assert manager.get_context_track_total(LIKED) == 5000
    assert manager.get_context_track(LIKED, 0).name == "Song 0"
    assert manager.get_context_track(LIKED, 4999) is None, "a library of 5000 must not be downloaded up front"
    assert len(manager.api.calls_to("/me/tracks")) == 1


def test_liked_songs_prefetch_the_next_page_near_the_boundary(manager):
    manager.api.saved_tracks = saved_track_payloads(200)
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)

    manager.ensure_context_tracks(LIKED, 45)
    drain(manager)

    assert manager.get_context_track(LIKED, 55).name == "Song 55"
    assert len(manager.api.calls_to("/me/tracks")) == 2


def test_a_captured_context_plays_the_selected_song_inside_it(manager):
    # What the Liked Songs dial does once the real context has been captured:
    # Spotify's own collection, starting on the song the dial is showing.
    poll(manager)

    manager.play_context_at("spotify:playlist:liked", "spotify:track:4cOdK2wGLETKBW3PvgPWqT")
    drain(manager)

    call = manager.api.calls_to("/me/player/play")[0]
    assert call.detail["context_uri"] == "spotify:playlist:liked"
    assert call.detail["offset"] == {"uri": "spotify:track:4cOdK2wGLETKBW3PvgPWqT"}
    assert call.detail["uris"] is None, "a context plays on past the song, a uris list does not"


def test_a_captured_context_can_be_played_from_the_top(manager):
    poll(manager)

    manager.play_context_at("spotify:playlist:liked")
    drain(manager)

    assert manager.api.calls_to("/me/player/play")[0].detail["offset"] is None


def test_junk_is_never_sent_as_a_context(manager):
    poll(manager)

    manager.play_context_at("not a spotify link", "spotify:track:t1")
    drain(manager)

    assert manager.api.calls_to("/me/player/play") == []


def test_playing_a_liked_song_carries_the_rest_of_the_library_with_it(manager):
    # Spotify has no context URI for Liked Songs, so the run is listed out;
    # playing one song must not leave the deck on a one-song queue.
    manager.api.saved_tracks = saved_track_payloads(200)
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)

    manager.play_run_from(LIKED, 3)
    drain(manager)

    call = manager.api.calls_to("/me/player/play")[0]
    uris = call.detail["uris"]
    assert uris[0] == "spotify:track:track0003", "the selected song plays first"
    assert uris[1:4] == [f"spotify:track:track{n:04d}" for n in (4, 5, 6)], "in browsing order"
    assert len(uris) == 50
    assert call.detail["context_uri"] is None


def test_a_liked_run_already_browsed_costs_no_extra_request(manager):
    manager.api.saved_tracks = saved_track_payloads(200)
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)
    before = len(manager.api.calls_to("/me/tracks"))

    manager.play_run_from(LIKED, 0)
    drain(manager)

    assert len(manager.api.calls_to("/me/tracks")) == before


def test_a_liked_run_past_the_cache_is_fetched(manager):
    manager.api.saved_tracks = saved_track_payloads(200)
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)

    manager.play_run_from(LIKED, 120)
    drain(manager)

    assert manager.api.calls_to("/me/player/play")[0].detail["uris"][0] == "spotify:track:track0120"


def test_a_liked_run_stops_at_the_end_of_the_library(manager):
    manager.api.saved_tracks = saved_track_payloads(12)
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)

    manager.play_run_from(LIKED, 9)
    drain(manager)

    uris = manager.api.calls_to("/me/player/play")[0].detail["uris"]
    assert uris == [f"spotify:track:track{n:04d}" for n in (9, 10, 11)]


# -- browsing any context (the Song Picker) --------------------------------


PLAYLIST = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
ALBUM = "spotify:album:1DFixLWuPkv3KT3TnV35m3"


def test_a_playlist_is_browsed_a_page_at_a_time(manager):
    manager.api.context_tracks = saved_track_payloads(120)

    manager.ensure_context_tracks(PLAYLIST, 0)
    drain(manager)

    assert manager.get_context_track_total(PLAYLIST) == 120
    assert manager.get_context_track(PLAYLIST, 0).name == "Song 0"
    assert manager.get_context_track(PLAYLIST, 119) is None, "only the first page should have been read"


def test_album_tracks_take_their_cover_from_the_album(manager):
    # An album listing gives bare tracks: no album, no images. Without the
    # context's own cover a picker would draw a placeholder for every song.
    manager.api.context_tracks = saved_track_payloads(4)
    manager.ensure_context_details(ALBUM)
    manager.ensure_context_tracks(ALBUM, 0)
    drain(manager)

    track = manager.get_context_track(ALBUM, 0)
    assert track.name == "Song 0"
    assert track.artwork_url == "https://i.example/context.jpg"


def test_a_browsed_context_is_not_re_requested(manager):
    manager.api.context_tracks = saved_track_payloads(60)

    for _ in range(4):
        manager.ensure_context_tracks(PLAYLIST, 0)
        drain(manager)

    assert len(manager.api.calls_to(Endpoints.playlist_items("37i9dQZF1DXcBWIGoYBM5M"))) == 1


def test_something_with_no_song_list_is_never_asked_for(manager):
    # An artist has no track listing and a bare track is not a collection.
    manager.ensure_context_tracks("spotify:artist:0TnOYISbd1XYRBk9myaseg", 0)
    manager.ensure_context_tracks("spotify:track:4cOdK2wGLETKBW3PvgPWqT", 0)

    assert drain(manager) == 0


# -- browsing the queue (the Queue Picker) ---------------------------------


def test_the_queue_is_what_is_playing_and_what_follows(manager):
    manager.api.queue = [track_payload(track_id="now", name="Now"), track_payload(track_id="next", name="Next")]

    manager.ensure_queue()
    drain(manager)

    assert [track.name for track in manager.get_queue_tracks()] == ["Now", "Next"]


def test_the_queue_is_read_once_until_something_changes(manager):
    manager.api.queue = [track_payload(track_id="now", name="Now")]
    manager.ensure_queue()
    drain(manager)

    manager.ensure_queue()
    assert drain(manager) == 0
    assert len(manager.api.calls_to(Endpoints.PLAYER_QUEUE)) == 1


def test_the_queue_is_re_read_when_the_song_changes(manager):
    # Nothing polls the queue, so a track change is what keeps it honest.
    manager.api.queue = [track_payload(track_id="now", name="Now")]
    manager.ensure_queue()
    settle(manager)
    before = len(manager.api.calls_to(Endpoints.PLAYER_QUEUE))

    manager.api.playback["item"] = track_payload(track_id="other", name="Something Else")
    settle(manager)

    assert len(manager.api.calls_to(Endpoints.PLAYER_QUEUE)) == before + 1


def test_the_queue_is_left_alone_when_no_picker_is_showing_it(manager):
    # The queue costs a request of its own, so it is only kept fresh for a
    # dial that has actually asked for it.
    settle(manager)
    manager.api.playback["item"] = track_payload(track_id="other", name="Something Else")
    settle(manager)

    assert manager.api.calls_to(Endpoints.PLAYER_QUEUE) == []


def test_playing_further_down_the_queue_skips_what_is_between(manager):
    # What the app does when you play something further down: it consumes the
    # songs in between, which keeps the context and everything past the twenty.
    manager.skip_to_queue_index(3)
    drain(manager)

    assert len(manager.api.calls_to("/me/player/next")) == 3
    assert manager.api.calls_to("/me/player/play") == [], "jumping is skipping, not a new queue"


def test_skipping_to_the_song_already_playing_does_nothing(manager):
    manager.skip_to_queue_index(0)

    assert drain(manager) == 0
    assert manager.api.calls_to("/me/player/next") == []


def test_playing_out_of_the_queue_keeps_what_was_behind_it(manager):
    tracks = [track_payload(track_id=f"q{n}", name=f"Q{n}") for n in range(4)]

    manager.play_tracks([track["uri"] for track in tracks[1:]])
    drain(manager)

    assert manager.api.calls_to("/me/player/play")[0].detail["uris"] == [
        "spotify:track:q1",
        "spotify:track:q2",
        "spotify:track:q3",
    ]


def test_an_empty_library_is_reported_as_empty(manager):
    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)
    assert manager.get_context_track_total(LIKED) == 0


def test_a_failed_page_can_be_retried(manager):
    manager.api.saved_tracks = saved_track_payloads(60)
    manager.api.raise_next = SpotifyNetworkError()

    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)
    assert manager.get_context_track_total(LIKED) is None

    manager.ensure_context_tracks(LIKED, 0)
    drain(manager)
    assert manager.get_context_track_total(LIKED) == 60


# -- devices ---------------------------------------------------------------


def test_device_refresh_notifies_only_on_change(manager):
    calls = []
    manager.add_listener(lambda: calls.append(1), {"devices"})

    manager._refresh_devices()
    assert len(calls) == 1

    manager._refresh_devices()
    assert len(calls) == 1

    manager.api.devices = [device_payload(), device_payload(device_id="phone", name="Phone", is_active=False)]
    manager._refresh_devices()
    assert len(calls) == 2


def test_a_fixed_device_that_disappears_is_reported_missing(manager):
    manager._refresh_devices()

    assert manager.resolve_device_id("specific", "device-1") == ("device-1", False)
    assert manager.resolve_device_id("specific", "gone") == ("gone", True)
    # Following the active device can never be "missing".
    assert manager.resolve_device_id("active", None) == (None, False)


# -- failures --------------------------------------------------------------


def test_no_active_device_is_not_an_error(manager):
    manager.api.raise_next = SpotifyNoDeviceError()
    manager.next_track()
    drain(manager)

    assert manager.last_error is None
    assert manager.get_status() is ActionStatus.NO_DEVICE


def test_rate_limiting_pauses_everything_for_the_retry_period(manager):
    manager.api.raise_next = SpotifyRateLimitError(30)
    manager.next_track()
    drain(manager)

    assert manager.is_rate_limited
    assert manager.get_status() is ActionStatus.RATE_LIMITED
    assert manager.rate_limited_until > time.monotonic() + 25


def test_network_errors_are_reported_as_offline_and_then_clear(manager):
    manager.api.raise_next = SpotifyNetworkError()
    manager.next_track()
    drain(manager)
    assert manager.get_status() is ActionStatus.OFFLINE

    manager.next_track()
    drain(manager)
    assert manager.get_status() is not ActionStatus.OFFLINE


def test_an_unexpected_exception_does_not_kill_the_worker(manager):
    manager.api.raise_next = ValueError("something odd")
    manager.next_track()
    drain(manager)

    assert manager.get_status() is ActionStatus.API_ERROR

    manager.next_track()
    drain(manager)
    assert manager.api.calls_to("/me/player/next")


def test_api_errors_are_surfaced(manager):
    manager.api.raise_next = SpotifyApiError(500)
    manager.next_track()
    drain(manager)

    assert manager.get_status() is ActionStatus.API_ERROR


# -- playing things --------------------------------------------------------


def test_playing_a_playlist_uses_a_context_uri(manager):
    manager.play_context("spotify:playlist:pl1")
    drain(manager)

    call = manager.api.calls_to("/me/player/play")[0]
    assert call.detail["context_uri"] == "spotify:playlist:pl1"
    assert call.detail["uris"] is None


def test_playing_a_single_track_uses_the_uris_list(manager):
    manager.play_context("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=x")
    drain(manager)

    call = manager.api.calls_to("/me/player/play")[0]
    assert call.detail["uris"] == ["spotify:track:4cOdK2wGLETKBW3PvgPWqT"]
    assert call.detail["context_uri"] is None


def test_an_unusable_link_never_becomes_a_request(manager):
    manager.play_context("https://example.com/not-spotify")
    drain(manager)
    assert manager.api.calls_to("/me/player/play") == []


def test_context_names_are_resolved_once_and_cached(manager):
    poll(manager)
    drain(manager)

    assert manager.get_context_name("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "Today's Top Hits"
    # The same lookup carries the cover, so a key showing one costs no request
    # of its own.
    assert manager.get_context_artwork_url("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "https://i.example/context.jpg"

    before = len(manager.api.calls)
    poll(manager)
    drain(manager)
    assert len(manager.api.calls_to("context:spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")) == 1
    assert len(manager.api.calls) >= before


def test_a_configured_link_can_be_resolved_without_playing_it(manager):
    # What the Play Context key needs: the cover for a link the user typed,
    # which has nothing to do with what is playing.
    uri = "spotify:playlist:configured"

    for _ in range(4):
        manager.ensure_context_details(uri)
        drain(manager)

    assert manager.get_context_artwork_url(uri) == "https://i.example/context.jpg"
    assert manager.get_context_name(uri) == "Today's Top Hits"
    assert len(manager.api.calls_to(f"context:{uri}")) == 1


def test_a_context_with_no_cover_is_not_asked_about_again(manager):
    uri = "spotify:playlist:bare"
    manager.api.get_context = lambda asked: {"name": None}

    manager.ensure_context_details(uri)
    drain(manager)
    manager.ensure_context_details(uri)

    assert drain(manager) == 0
    assert manager.get_context_artwork_url(uri) is None


# -- lifecycle -------------------------------------------------------------


def test_shutdown_stops_everything_and_leaves_no_threads(tmp_path):
    api = FakeSpotifyApi()
    token_path = tmp_path / "spotify-auth.json"
    TokenStore(str(token_path)).save(TokenSet("access", "refresh", time.time() + 3600))

    instance = SpotifyManager(
        token_path=str(token_path),
        settings_provider=lambda: {"spotify_client_id": "abc"},
        api=api,
    )
    instance.add_listener(lambda: None, {"playback"})
    time.sleep(0.1)

    instance.shutdown()

    assert instance._queue.is_closed
    assert all(not thread.is_alive() for thread in instance._threads)
    assert api.closed


def test_signing_out_clears_the_cached_account_state(manager):
    poll(manager)
    drain(manager)
    assert manager.get_playback_state().track is not None

    manager.auth.disconnect()

    assert manager.get_playback_state().track is None
    assert manager.get_devices() == []
    assert manager.get_status() is ActionStatus.AUTH_REQUIRED


# -- an idle Spotify -------------------------------------------------------
#
# Opening Spotify without pressing play leaves the desktop client listed as an
# available device while /me/player returns nothing. Everything below is about
# that state being usable rather than a dead end.


def _idle(manager) -> None:
    """Spotify open, a device present, nothing playing."""
    manager.api.playback = None
    manager.api.devices = [device_payload(is_active=False, volume=40)]
    manager._refresh_devices()
    poll(manager)


def test_an_idle_device_is_not_reported_as_no_device(manager):
    _idle(manager)
    assert manager.get_status() is ActionStatus.READY


def test_genuinely_no_devices_is_still_no_device(manager):
    manager.api.playback = None
    manager.api.devices = []
    manager._refresh_devices()
    poll(manager)

    assert manager.get_status() is ActionStatus.NO_DEVICE


def test_play_names_the_idle_device_so_it_has_somewhere_to_land(manager):
    _idle(manager)

    manager.play()
    drain(manager)

    # Without an explicit device Spotify would answer 404: it has no active one.
    assert manager.api.calls_to("/me/player/play")[0].detail["device_id"] == "device-1"


def test_commands_leave_the_device_to_spotify_while_it_is_playing(manager):
    settle(manager)

    manager.next_track()
    drain(manager)

    assert manager.api.calls_to("/me/player/next")[0].detail["device_id"] is None


def test_a_fixed_target_is_always_honoured(manager):
    _idle(manager)
    manager.api.devices.append(device_payload(device_id="phone", name="Phone", is_active=False))
    manager._refresh_devices()

    manager.pause(device_id="phone")
    drain(manager)

    assert manager.api.calls_to("/me/player/pause")[0].detail["device_id"] == "phone"


def test_an_active_device_wins_over_the_rest(manager):
    manager.api.devices = [
        device_payload(device_id="phone", name="Phone", is_active=False),
        device_payload(device_id="desktop", name="Desktop", is_active=True),
    ]
    manager._refresh_devices()

    assert manager.preferred_device_id() == "desktop"


def test_restricted_devices_are_never_chosen(manager):
    manager.api.devices = [device_payload(device_id="tv", is_active=False, is_restricted=True)]
    manager._refresh_devices()

    assert manager.preferred_device_id() is None


def test_volume_reads_and_writes_the_idle_device(manager):
    _idle(manager)

    # The level comes from the device list, since there is no playback to read.
    assert manager.get_volume() == 40
    assert manager.supports_volume()

    manager.adjust_volume(5)
    drain(manager)

    call = manager.api.calls_to(VOLUME_PATH)[0]
    assert call.detail["volume_percent"] == 45
    assert call.detail["device_id"] == "device-1"


def test_playing_a_playlist_wakes_an_idle_device(manager):
    _idle(manager)

    manager.play_context("spotify:playlist:pl1")
    drain(manager)

    call = manager.api.calls_to("/me/player/play")[0]
    assert call.detail["context_uri"] == "spotify:playlist:pl1"
    assert call.detail["device_id"] == "device-1"


# -- the queue -------------------------------------------------------------


def test_queueing_a_song_reports_success(manager):
    poll(manager)
    results = []

    manager.add_to_queue("spotify:track:t9", on_result=results.append)
    drain(manager)

    assert manager.api.queued == ["spotify:track:t9"]
    assert results == [True]
    # Queueing must not disturb what is playing.
    assert manager.api.calls_to("/me/player/play") == []


def test_queueing_goes_to_the_configured_device(manager):
    _idle(manager)

    manager.add_to_queue("spotify:track:t9", "device-1")
    drain(manager)

    assert manager.api.calls_to(Endpoints.PLAYER_QUEUE)[0].detail["device_id"] == "device-1"


def test_a_refused_queue_is_reported_as_a_failure(manager):
    # settle() so the lookups a first poll queues cannot swallow the failure.
    settle(manager)
    manager.api.raise_next = SpotifyNoDeviceError()
    results = []

    manager.add_to_queue("spotify:track:t9", on_result=results.append)
    drain(manager)

    assert results == [False], "the key has to be able to show that it did not work"


def test_queueing_nothing_fails_without_a_request(manager):
    results = []

    manager.add_to_queue("", on_result=results.append)
    drain(manager)

    assert results == [False]
    assert manager.api.calls_to(Endpoints.PLAYER_QUEUE) == []


# -- opening links ---------------------------------------------------------


def _record_launches(manager, succeed_on=None, running_client=False):
    """Replace both hand-offs with recorders. Returns what was tried.

    The MPRIS one is always replaced: left alone it would talk to whatever
    Spotify client happens to be running on the machine running the tests.
    """
    tried: list[str] = []

    def launch(target: str) -> bool:
        tried.append(target)
        return succeed_on is None or target.startswith(succeed_on)

    def show(uri: str) -> bool:
        if not running_client:
            return False
        tried.append(f"mpris:{uri}")
        return True

    manager._launch_uri = launch
    manager._show_in_running_app = show
    return tried


def test_opening_a_track_goes_to_the_app_first(manager):
    tried = _record_launches(manager)

    assert manager.open_in_spotify("spotify:track:t1")
    # The app took it, so the web player was never tried.
    assert tried == ["spotify:track:t1"]


def test_a_running_client_is_spoken_to_rather_than_launched(manager):
    # Launching hands the URI to the client's command line, which makes it drop
    # what it is playing; a client that is already up is asked directly.
    tried = _record_launches(manager, running_client=True)

    assert manager.open_in_spotify("spotify:track:t1")
    assert tried == ["mpris:spotify:track:t1"]


def test_the_web_player_catches_a_missing_app(manager):
    tried = _record_launches(manager, succeed_on="https://")

    assert manager.open_in_spotify("spotify:track:t1")
    assert tried == ["spotify:track:t1", "https://open.spotify.com/track/t1"]


def test_the_browser_can_be_chosen_instead(manager):
    manager._settings_provider = lambda: {"open_links_in_app": False}
    tried = _record_launches(manager)

    assert manager.open_in_spotify("spotify:track:t1")
    assert tried == ["https://open.spotify.com/track/t1"]


def test_opening_nothing_fails_without_trying(manager):
    tried = _record_launches(manager)

    assert not manager.open_in_spotify(None)
    assert not manager.open_in_spotify("")
    assert tried == []


def test_a_restore_point_describes_where_playback_is(manager):
    poll(manager)
    state = manager.get_playback_state()
    restore = manager._restore_point()

    assert restore["context_uri"] == state.context_uri
    assert restore["track_uri"] == state.track.uri
    assert restore["was_playing"] is True
    assert restore["position_ms"] >= state.progress_ms


def test_there_is_no_restore_point_without_a_context_to_restore_into(manager):
    # Liked Songs and a bare track cannot be handed back to `play` as a
    # context, and restoring with `uris` would flatten the queue to one track.
    poll(manager)
    manager._playback = replace(manager._playback, context_uri="spotify:collection:tracks")
    assert manager._restore_point() is None

    manager.api.playback = None
    poll(manager)
    assert manager._restore_point() is None


def test_navigating_parks_playback_and_puts_it_back(manager):
    manager.navigate_settle_seconds = 0
    poll(manager)
    restore = manager._restore_point()
    navigated = []

    manager._navigate_around_playback(lambda: navigated.append("open"), restore)

    assert navigated == ["open"]
    # Paused first so the client cannot collapse mid-track, then given the same
    # context, track and place back.
    assert manager.api.paths()[-2:] == ["/me/player/pause", "/me/player/play"]
    resumed = manager.api.calls_to("/me/player/play")[-1]
    assert resumed.detail["context_uri"] == restore["context_uri"]
    assert resumed.detail["device_id"] == restore["device_id"]


def test_a_paused_player_is_never_asked_to_pause_again(manager):
    # Spotify answers a pointless pause with 403 "Restriction violated", and
    # that used to abort the navigation: the app came forward showing nothing.
    manager.navigate_settle_seconds = 0
    manager.api.playback["is_playing"] = False
    manager.api.playback["actions"] = {"disallows": {"pausing": True}}
    poll(manager)

    manager._navigate_around_playback(lambda: None, manager._restore_point())

    assert manager.api.paths()[-2:] == ["/me/player/play", "/me/player/pause"]
    assert len(manager.api.calls_to("/me/player/pause")) == 1, "only the one that puts it back as it was"


def test_a_refused_pause_does_not_stop_the_navigation(manager):
    # Whatever Spotify thinks of the pause, the item still has to open.
    manager.navigate_settle_seconds = 0
    poll(manager)

    def refuse(device_id=None):
        raise SpotifyRestrictedError("Player command failed: Restriction violated")

    manager.api.pause = refuse
    navigated = []

    manager._navigate_around_playback(lambda: navigated.append("open"), manager._restore_point())

    assert navigated == ["open"], "the open must survive a refused pause"
    assert manager.api.calls_to("/me/player/play"), "and playback still gets put back"


def test_playback_comes_back_even_if_the_navigation_fails(manager):
    manager.navigate_settle_seconds = 0
    poll(manager)

    def boom():
        raise RuntimeError("no client")

    with pytest.raises(RuntimeError):
        manager._navigate_around_playback(boom, manager._restore_point())

    assert manager.api.paths()[-1] == "/me/player/play"


def test_a_live_session_is_never_navigated_away_from(manager):
    # Telling the client to open something wedges it at the end of the current
    # track, so anything playing — or paused, which still holds a queue — means
    # the window is only raised.
    assert manager._playback_is_live(), "nothing polled yet: assume there is something to lose"

    poll(manager)
    assert manager._playback_is_live()

    manager.api.playback["is_playing"] = False
    poll(manager)
    assert manager._playback_is_live()

    manager.api.playback = None
    poll(manager)
    assert not manager._playback_is_live()


# -- the profile -----------------------------------------------------------


def test_the_profile_is_fetched_when_it_is_missing(manager):
    # Starting up with a token already on disk is not an auth *change*, so
    # nothing else would ever ask for /me.
    assert manager.profile is None

    manager.ensure_profile()
    drain(manager)

    assert manager.profile is not None
    assert len(manager.api.calls_to(Endpoints.ME)) == 1


def test_the_profile_is_fetched_once_not_per_poll(manager):
    # The poll loop asks on every pass; only the first one costs a request.
    for _ in range(5):
        manager.ensure_profile()
    drain(manager)
    for _ in range(5):
        manager.ensure_profile()
    drain(manager)

    assert len(manager.api.calls_to(Endpoints.ME)) == 1


def test_a_failed_profile_fetch_is_retried_later_not_immediately(manager):
    manager.api.raise_next = SpotifyApiError(500)

    manager.ensure_profile()
    drain(manager)
    assert manager.profile is None

    # Still backing off, so a second ask costs nothing.
    manager.ensure_profile()
    assert drain(manager) == 0

    manager._profile_retry_after = 0.0
    manager.ensure_profile()
    drain(manager)

    assert manager.profile is not None
