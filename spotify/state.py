"""Pure rules shared by the manager, the actions and the renderers.

Everything here is a function of its arguments — no network, no threads, no
StreamController — which is what makes the interaction rules (what a dial turn
means, where a seek lands, which repeat mode comes next) testable without a
Spotify account or a deck.
"""

from __future__ import annotations

import time
from enum import Enum

from .models import (
    ITEM_TYPE_TRACK,
    REPEAT_CONTEXT,
    REPEAT_OFF,
    REPEAT_TRACK,
    PlaybackState,
)


class ActionStatus(Enum):
    """What an action should be telling the user right now."""

    READY = "ready"
    SUCCESS = "success"
    PENDING = "pending"
    BUSY = "busy"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    NO_DEVICE = "no_device"
    AUTH_REQUIRED = "auth_required"
    API_ERROR = "api_error"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class LikeState(Enum):
    UNKNOWN = "unknown"
    LIKED = "liked"
    NOT_LIKED = "not_liked"
    BUSY = "busy"


#: Volume is a percentage everywhere in this plugin, never a raw device value.
VOLUME_MIN = 0
VOLUME_MAX = 100

#: Used when unmuting a device whose pre-mute volume was never observed.
FALLBACK_UNMUTE_VOLUME = 50


def clamp_volume(percent: float | int | None) -> int:
    if percent is None:
        return 0
    return max(VOLUME_MIN, min(VOLUME_MAX, int(round(float(percent)))))


def clamp_position(position_ms: float | int | None, duration_ms: int | None) -> int:
    """Keep a seek inside the item: never below zero, never past the end."""
    position = max(0, int(position_ms or 0))
    if duration_ms:
        # One second of headroom, because seeking to exactly the duration makes
        # Spotify skip to the next item, which is not what a seek button means.
        position = min(position, max(0, int(duration_ms) - 1000))
    return position


def next_repeat_mode(current: str | None) -> str:
    """off -> context -> track -> off (PRD's recommended cycle)."""
    if current == REPEAT_OFF or current is None:
        return REPEAT_CONTEXT
    if current == REPEAT_CONTEXT:
        return REPEAT_TRACK
    return REPEAT_OFF


def toggle_repeat_mode(current: str | None, mode: str) -> str:
    """Turn `mode` on, or off again if it is already the active mode."""
    return REPEAT_OFF if current == mode else mode


def interpolated_progress_ms(state: PlaybackState | None, now: float | None = None) -> int | None:
    """Where playback has reached, without asking Spotify.

    The poll only runs about once a second, so a progress bar drawn straight
    from `progress_ms` visibly stutters. Advancing the last known position by
    the elapsed monotonic time gives a smooth bar that reconciles on the next
    real response.
    """
    if state is None or state.progress_ms is None:
        return None

    if not state.is_playing:
        return state.progress_ms

    now = time.monotonic() if now is None else now
    elapsed_ms = max(0.0, (now - state.last_updated_monotonic) * 1000.0)
    position = int(state.progress_ms + elapsed_ms)

    if state.duration_ms:
        position = min(position, state.duration_ms)
    return position


def progress_fraction(state: PlaybackState | None, now: float | None = None) -> float | None:
    duration = state.duration_ms if state else None
    if not duration:
        return None
    position = interpolated_progress_ms(state, now)
    if position is None:
        return None
    return max(0.0, min(1.0, position / duration))


def seek_target_ms(state: PlaybackState | None, delta_ms: int, now: float | None = None) -> int:
    """Absolute position for a relative seek from where playback is now."""
    current = interpolated_progress_ms(state, now) or 0
    duration = state.duration_ms if state else None
    return clamp_position(current + delta_ms, duration)


def format_duration(ms: int | None) -> str:
    """m:ss, or h:mm:ss for long podcast episodes."""
    if ms is None or ms < 0:
        return "--:--"
    total_seconds = int(ms // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def device_supports_volume(state: PlaybackState | None) -> bool:
    device = state.device if state else None
    if device is None:
        return False
    return device.supports_volume and not device.is_restricted


def is_music_track(state: PlaybackState | None) -> bool:
    """Whether music-only actions (Like, Add to Playlist) apply right now."""
    track = state.track if state else None
    if track is None:
        return False
    return track.item_type == ITEM_TYPE_TRACK and track.supports_library


def playback_status(
    *,
    authenticated: bool,
    rate_limited_until: float | None,
    last_error: Exception | None,
    state: PlaybackState | None,
    has_available_device: bool = False,
    now: float | None = None,
) -> ActionStatus:
    """The status every playback action derives its fallback rendering from.

    Ordered by how much it matters to the user: being logged out explains more
    than a stale error, which explains more than an idle device.
    """
    if not authenticated:
        return ActionStatus.AUTH_REQUIRED

    now = time.monotonic() if now is None else now
    if rate_limited_until and rate_limited_until > now:
        return ActionStatus.RATE_LIMITED

    if last_error is not None:
        # Imported lazily to keep this module free of import cycles.
        from .errors import SpotifyApiError, SpotifyNetworkError

        if isinstance(last_error, SpotifyNetworkError):
            return ActionStatus.OFFLINE
        if isinstance(last_error, SpotifyApiError):
            return ActionStatus.API_ERROR

    if state is None:
        return ActionStatus.PENDING

    device = state.device
    if device is not None and device.is_restricted:
        return ActionStatus.UNAVAILABLE

    if not state.has_playback or device is None:
        # Spotify open but idle still counts as controllable: the device is
        # there, and pressing play is how you start it. Only report NO_DEVICE
        # when there is genuinely nothing to talk to.
        return ActionStatus.READY if has_available_device else ActionStatus.NO_DEVICE

    return ActionStatus.READY
