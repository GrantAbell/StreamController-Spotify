"""The manager, driven against the fake API.

The worker threads are not started here: the queue is drained by hand so each
test can say exactly what happened, in what order, and how many requests it
took. That is the only way to assert things like "a fast dial spin sends two
requests, not forty".
"""

from __future__ import annotations

import time

import pytest

from spotify_essentials.spotify.auth import TokenSet, TokenStore
from spotify_essentials.spotify.errors import (
    SpotifyApiError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyRateLimitError,
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

    manager.ensure_liked_songs(0)
    drain(manager)

    assert manager.get_liked_songs_total() == 5000
    assert manager.get_liked_song(0).name == "Song 0"
    assert manager.get_liked_song(4999) is None, "a library of 5000 must not be downloaded up front"
    assert len(manager.api.calls_to("/me/tracks")) == 1


def test_liked_songs_prefetch_the_next_page_near_the_boundary(manager):
    manager.api.saved_tracks = saved_track_payloads(200)
    manager.ensure_liked_songs(0)
    drain(manager)

    manager.ensure_liked_songs(45)
    drain(manager)

    assert manager.get_liked_song(55).name == "Song 55"
    assert len(manager.api.calls_to("/me/tracks")) == 2


def test_an_empty_library_is_reported_as_empty(manager):
    manager.ensure_liked_songs(0)
    drain(manager)
    assert manager.get_liked_songs_total() == 0


def test_a_failed_page_can_be_retried(manager):
    manager.api.saved_tracks = saved_track_payloads(60)
    manager.api.raise_next = SpotifyNetworkError()

    manager.ensure_liked_songs(0)
    drain(manager)
    assert manager.get_liked_songs_total() is None

    manager.ensure_liked_songs(0)
    drain(manager)
    assert manager.get_liked_songs_total() == 60


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

    before = len(manager.api.calls)
    poll(manager)
    drain(manager)
    assert len(manager.api.calls_to("context:spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")) == 1
    assert len(manager.api.calls) >= before


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
