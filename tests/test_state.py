"""The interaction rules: volume, seek, repeat cycling and status precedence."""

from __future__ import annotations

import time

from spotify_essentials.spotify.errors import (
    SpotifyApiError,
    SpotifyNetworkError,
)
from spotify_essentials.spotify.models import (
    REPEAT_CONTEXT,
    REPEAT_OFF,
    REPEAT_TRACK,
    PlaybackState,
    parse_playback,
)
from spotify_essentials.spotify.state import (
    ActionStatus,
    clamp_position,
    clamp_volume,
    device_supports_volume,
    format_duration,
    interpolated_progress_ms,
    is_music_track,
    next_repeat_mode,
    playback_status,
    progress_fraction,
    seek_target_ms,
    toggle_repeat_mode,
)

from .fakes import device_payload, episode_payload, playback_payload, track_payload


# -- volume ----------------------------------------------------------------


def test_volume_clamping():
    assert clamp_volume(0) == 0
    assert clamp_volume(100) == 100
    assert clamp_volume(-20) == 0
    assert clamp_volume(140) == 100
    assert clamp_volume(None) == 0
    assert clamp_volume(62.6) == 63


def test_volume_support_requires_an_unrestricted_device():
    supported = parse_playback(playback_payload())
    assert device_supports_volume(supported)

    unsupported = parse_playback(playback_payload(device=device_payload(supports_volume=False)))
    assert not device_supports_volume(unsupported)

    restricted = parse_playback(playback_payload(device=device_payload(is_restricted=True)))
    assert not device_supports_volume(restricted)

    assert not device_supports_volume(parse_playback(None))


# -- seeking ---------------------------------------------------------------


def test_seek_never_goes_below_zero():
    assert clamp_position(-5000, 200000) == 0


def test_seek_stops_short_of_the_end():
    # Seeking exactly to the duration makes Spotify skip on, which a seek
    # button must not do.
    assert clamp_position(200000, 200000) == 199000
    assert clamp_position(500000, 200000) == 199000


def test_seek_without_a_known_duration_is_only_floored():
    assert clamp_position(999999, None) == 999999


def test_relative_seek_from_the_current_position():
    state = parse_playback(playback_payload(progress_ms=42000, is_playing=False))

    assert seek_target_ms(state, 5000) == 47000
    assert seek_target_ms(state, -5000) == 37000
    assert seek_target_ms(state, -100000) == 0


# -- progress interpolation ------------------------------------------------


def test_progress_advances_locally_while_playing():
    state = parse_playback(playback_payload(progress_ms=42000, is_playing=True))
    later = state.last_updated_monotonic + 2.0

    assert interpolated_progress_ms(state, now=later) == 44000


def test_progress_is_frozen_while_paused():
    state = parse_playback(playback_payload(progress_ms=42000, is_playing=False))
    later = state.last_updated_monotonic + 30.0

    assert interpolated_progress_ms(state, now=later) == 42000


def test_progress_is_clamped_to_the_track_length():
    state = parse_playback(playback_payload(progress_ms=199000, is_playing=True))
    later = state.last_updated_monotonic + 60.0

    assert interpolated_progress_ms(state, now=later) == 200040


def test_progress_fraction_needs_a_duration():
    assert progress_fraction(parse_playback(None)) is None
    fraction = progress_fraction(parse_playback(playback_payload(progress_ms=100020, is_playing=False)))
    assert 0.49 < fraction < 0.51


# -- repeat ----------------------------------------------------------------


def test_repeat_cycles_off_context_track():
    assert next_repeat_mode(REPEAT_OFF) == REPEAT_CONTEXT
    assert next_repeat_mode(REPEAT_CONTEXT) == REPEAT_TRACK
    assert next_repeat_mode(REPEAT_TRACK) == REPEAT_OFF
    assert next_repeat_mode(None) == REPEAT_CONTEXT


def test_repeat_toggle_turns_its_own_mode_off():
    assert toggle_repeat_mode(REPEAT_OFF, REPEAT_CONTEXT) == REPEAT_CONTEXT
    assert toggle_repeat_mode(REPEAT_CONTEXT, REPEAT_CONTEXT) == REPEAT_OFF
    assert toggle_repeat_mode(REPEAT_TRACK, REPEAT_CONTEXT) == REPEAT_CONTEXT


# -- item types ------------------------------------------------------------


def test_music_only_actions_reject_non_tracks():
    assert is_music_track(parse_playback(playback_payload()))
    assert not is_music_track(parse_playback(playback_payload(item=episode_payload())))
    assert not is_music_track(parse_playback(playback_payload(item=track_payload(is_local=True))))
    assert not is_music_track(parse_playback(None))


# -- formatting ------------------------------------------------------------


def test_duration_formatting():
    assert format_duration(0) == "0:00"
    assert format_duration(65000) == "1:05"
    assert format_duration(3_600_000) == "1:00:00"
    assert format_duration(None) == "--:--"


# -- status ----------------------------------------------------------------


def _status(**kwargs) -> ActionStatus:
    defaults = {
        "authenticated": True,
        "rate_limited_until": None,
        "last_error": None,
        "state": parse_playback(playback_payload()),
        "now": time.monotonic(),
    }
    defaults.update(kwargs)
    return playback_status(**defaults)


def test_status_reports_normal_playback_as_ready():
    assert _status() is ActionStatus.READY


def test_authentication_outranks_everything():
    assert _status(authenticated=False, last_error=SpotifyNetworkError()) is ActionStatus.AUTH_REQUIRED


def test_rate_limiting_outranks_errors():
    now = time.monotonic()
    assert _status(rate_limited_until=now + 30, last_error=SpotifyApiError(500), now=now) is ActionStatus.RATE_LIMITED


def test_expired_rate_limit_is_ignored():
    now = time.monotonic()
    assert _status(rate_limited_until=now - 1, now=now) is ActionStatus.READY


def test_network_and_api_errors_are_distinguished():
    assert _status(last_error=SpotifyNetworkError()) is ActionStatus.OFFLINE
    assert _status(last_error=SpotifyApiError(500)) is ActionStatus.API_ERROR


def test_restricted_device_is_unavailable():
    state = parse_playback(playback_payload(device=device_payload(is_restricted=True)))
    assert _status(state=state) is ActionStatus.UNAVAILABLE


def test_no_playback_is_no_device():
    assert _status(state=parse_playback(None)) is ActionStatus.NO_DEVICE


def test_missing_state_is_pending():
    assert _status(state=None) is ActionStatus.PENDING


def test_playback_state_defaults_are_safe():
    empty = PlaybackState()
    assert empty.track is None
    assert empty.allows("resuming")


def test_an_idle_but_available_device_is_ready_not_no_device():
    # Spotify open with nothing playing: the controls work, and pressing play is
    # how you start it.
    idle = _status(state=parse_playback(None), has_available_device=True)
    assert idle is ActionStatus.READY

    assert _status(state=parse_playback(None), has_available_device=False) is ActionStatus.NO_DEVICE
