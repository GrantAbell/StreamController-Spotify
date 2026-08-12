"""Normalised errors for everything below the manager.

Actions only ever see these types. Nothing from `requests` escapes the API
client, so an action callback can never be handed an exception whose meaning
depends on the HTTP library.
"""

from __future__ import annotations


class SpotifyPluginError(Exception):
    """Base class for every error this plugin raises."""


class SpotifyAuthError(SpotifyPluginError):
    """Not authenticated, or the stored grant is no longer usable."""

    def __init__(self, message: str = "Spotify authentication required", *, needs_reauth: bool = True):
        super().__init__(message)
        self.needs_reauth = needs_reauth


class SpotifyRateLimitError(SpotifyPluginError):
    """Spotify returned 429. `retry_after` is seconds, from the header."""

    def __init__(self, retry_after: float, message: str = "Spotify rate limited"):
        super().__init__(f"{message} for {retry_after:.0f}s")
        self.retry_after = retry_after


class SpotifyNoDeviceError(SpotifyPluginError):
    """Authenticated, but no device is available to accept the command."""

    def __init__(self, message: str = "No active Spotify device"):
        super().__init__(message)


class SpotifyRestrictedError(SpotifyPluginError):
    """The device or the current item refuses this operation.

    Covers both a device advertising `is_restricted` and a 403 from Spotify for
    an action the current context disallows.
    """

    def __init__(self, message: str = "Spotify refused this action"):
        super().__init__(message)


class SpotifyNetworkError(SpotifyPluginError):
    """DNS failure, connection refused, timeout — nothing reached Spotify."""

    def __init__(self, message: str = "Could not reach Spotify"):
        super().__init__(message)


class SpotifyApiError(SpotifyPluginError):
    """Any other non-success HTTP status."""

    def __init__(self, status_code: int, message: str = None):
        super().__init__(message or f"Spotify API error {status_code}")
        self.status_code = status_code


class SpotifyShutdownError(SpotifyPluginError):
    """The plugin is shutting down and refused to start new work."""
