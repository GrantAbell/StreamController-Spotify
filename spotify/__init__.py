"""Spotify domain layer: auth, HTTP, models, caches and the one manager.

Nothing in this package imports StreamController or GTK, so all of it can be
exercised in a plain test process against a fake API.
"""

from .errors import (
    SpotifyApiError,
    SpotifyAuthError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyPluginError,
    SpotifyRateLimitError,
    SpotifyRestrictedError,
)
from .manager import SpotifyManager
from .models import PlaybackState, SpotifyDevice, SpotifyPlaylist, SpotifyTrack, UserProfile
from .state import ActionStatus, LikeState

__all__ = [
    "ActionStatus",
    "LikeState",
    "PlaybackState",
    "SpotifyApiError",
    "SpotifyAuthError",
    "SpotifyDevice",
    "SpotifyManager",
    "SpotifyNetworkError",
    "SpotifyNoDeviceError",
    "SpotifyPlaylist",
    "SpotifyPluginError",
    "SpotifyRateLimitError",
    "SpotifyRestrictedError",
    "SpotifyTrack",
    "UserProfile",
]
