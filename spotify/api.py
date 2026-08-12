"""The only module in the plugin that speaks HTTP to Spotify.

Every path lives in `Endpoints` so the whole surface can be re-checked against
Spotify's current API reference in one place. Responses are decoded into models
or into the plugin's own exceptions — no `requests.Response` ever leaves here.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

import requests

from .errors import (
    SpotifyApiError,
    SpotifyAuthError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyRateLimitError,
    SpotifyRestrictedError,
    SpotifyShutdownError,
)
from .log import debug, log

API_BASE = "https://api.spotify.com/v1"

#: (connect, read). A hung request must never outlive a shutdown request.
DEFAULT_TIMEOUT = (3.05, 5)

#: Spotify's ceiling for the paginated collections used here.
MAX_PAGE_SIZE = 50


class Endpoints:
    """Current Spotify paths, gathered for the pre-release freshness check.

    The library and playlist-item routes are the 2026 generic forms. The older
    `/me/tracks` mutation and `contains` routes, and `/playlists/{id}/tracks`,
    are deliberately absent — a test asserts they never come back.
    """

    PLAYER = "/me/player"
    PLAYER_DEVICES = "/me/player/devices"
    PLAYER_PLAY = "/me/player/play"
    PLAYER_PAUSE = "/me/player/pause"
    PLAYER_NEXT = "/me/player/next"
    PLAYER_PREVIOUS = "/me/player/previous"
    PLAYER_SEEK = "/me/player/seek"
    PLAYER_REPEAT = "/me/player/repeat"
    PLAYER_SHUFFLE = "/me/player/shuffle"
    PLAYER_VOLUME = "/me/player/volume"

    ME = "/me"

    LIBRARY = "/me/library"
    LIBRARY_CONTAINS = "/me/library/contains"
    SAVED_TRACKS = "/me/tracks"

    PLAYLISTS = "/me/playlists"

    @staticmethod
    def playlist_items(playlist_id: str) -> str:
        return f"/playlists/{playlist_id}/items"

    @staticmethod
    def playlist(playlist_id: str) -> str:
        return f"/playlists/{playlist_id}"

    @staticmethod
    def album(album_id: str) -> str:
        return f"/albums/{album_id}"

    @staticmethod
    def artist(artist_id: str) -> str:
        return f"/artists/{artist_id}"

    @staticmethod
    def show(show_id: str) -> str:
        return f"/shows/{show_id}"


@runtime_checkable
class SpotifyApiProtocol(Protocol):
    """What the manager needs. `FakeSpotifyApi` in the tests implements this."""

    def get_playback_state(self) -> dict | None: ...
    def get_devices(self) -> dict: ...
    def get_profile(self) -> dict: ...

    def play(
        self,
        device_id: str | None = None,
        context_uri: str | None = None,
        uris: list[str] | None = None,
        position_ms: int | None = None,
        offset: dict | None = None,
    ) -> None: ...
    def pause(self, device_id: str | None = None) -> None: ...
    def next_track(self, device_id: str | None = None) -> None: ...
    def previous_track(self, device_id: str | None = None) -> None: ...
    def seek(self, position_ms: int, device_id: str | None = None) -> None: ...
    def set_repeat(self, mode: str, device_id: str | None = None) -> None: ...
    def set_shuffle(self, enabled: bool, device_id: str | None = None) -> None: ...
    def set_volume(self, percent: int, device_id: str | None = None) -> None: ...
    def transfer_playback(self, device_id: str, play: bool = False) -> None: ...

    def library_contains(self, uris: list[str]) -> list[bool]: ...
    def library_save(self, uris: list[str]) -> None: ...
    def library_remove(self, uris: list[str]) -> None: ...

    def get_saved_tracks(self, limit: int = MAX_PAGE_SIZE, offset: int = 0) -> dict: ...
    def get_playlists(self, limit: int = MAX_PAGE_SIZE, offset: int = 0) -> dict: ...
    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None: ...

    def get_context(self, uri: str) -> dict | None: ...


class SpotifyApiClient:
    def __init__(self, auth_manager, session: requests.Session | None = None):
        self._auth = auth_manager
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._shutdown = threading.Event()

    # -- transport --------------------------------------------------------

    def close(self) -> None:
        self._shutdown.set()
        if self._owns_session:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        allow_retry: bool = True,
    ) -> Any:
        if self._shutdown.is_set():
            raise SpotifyShutdownError("Spotify client is shutting down")

        token = self._auth.get_access_token()
        url = f"{API_BASE}{path}"

        debug(f"Spotify {method} {path}")

        try:
            response = self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout as error:
            raise SpotifyNetworkError("Spotify did not respond in time") from error
        except requests.RequestException as error:
            raise SpotifyNetworkError("Could not reach Spotify") from error

        status = response.status_code

        if status == 401 and allow_retry:
            # The token expired earlier than expected, or was revoked. Exactly
            # one refresh-and-retry, so a permanently rejected grant cannot turn
            # into a refresh loop.
            try:
                self._auth.get_access_token(force_refresh=True)
            except SpotifyAuthError:
                self._auth.mark_invalid()
                raise
            return self._request(method, path, params=params, json_body=json_body, allow_retry=False)

        if status == 401:
            self._auth.mark_invalid()
            raise SpotifyAuthError("Spotify rejected the stored login")

        if status == 429:
            raise SpotifyRateLimitError(_retry_after_seconds(response))

        if status == 403:
            raise SpotifyRestrictedError(_error_message(response) or "Spotify refused this action")

        if status == 404:
            reason = _error_reason(response)
            if reason in ("NO_ACTIVE_DEVICE", "NO_PREV_TRACK", "NO_NEXT_TRACK") or _looks_like_no_device(response):
                raise SpotifyNoDeviceError()
            raise SpotifyApiError(404, _error_message(response) or "Spotify could not find that item")

        if status == 204 or not response.content:
            return None

        if status >= 400:
            raise SpotifyApiError(status, _error_message(response))

        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _device_params(device_id: str | None) -> dict | None:
        return {"device_id": device_id} if device_id else None

    # -- playback ---------------------------------------------------------

    def get_playback_state(self) -> dict | None:
        # `additional_types` is what makes podcast episodes appear as episodes
        # instead of a null item.
        return self._request("GET", Endpoints.PLAYER, params={"additional_types": "track,episode"})

    def get_devices(self) -> dict:
        return self._request("GET", Endpoints.PLAYER_DEVICES) or {}

    def get_profile(self) -> dict:
        return self._request("GET", Endpoints.ME) or {}

    def play(
        self,
        device_id: str | None = None,
        context_uri: str | None = None,
        uris: list[str] | None = None,
        position_ms: int | None = None,
        offset: dict | None = None,
    ) -> None:
        body: dict = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        if offset:
            body["offset"] = offset
        if position_ms is not None:
            body["position_ms"] = int(position_ms)

        # An empty body means "resume whatever was playing", which is different
        # from starting something new, so it must stay empty rather than {}.
        self._request(
            "PUT",
            Endpoints.PLAYER_PLAY,
            params=self._device_params(device_id),
            json_body=body or None,
        )

    def pause(self, device_id: str | None = None) -> None:
        self._request("PUT", Endpoints.PLAYER_PAUSE, params=self._device_params(device_id))

    def next_track(self, device_id: str | None = None) -> None:
        self._request("POST", Endpoints.PLAYER_NEXT, params=self._device_params(device_id))

    def previous_track(self, device_id: str | None = None) -> None:
        self._request("POST", Endpoints.PLAYER_PREVIOUS, params=self._device_params(device_id))

    def seek(self, position_ms: int, device_id: str | None = None) -> None:
        params = {"position_ms": int(max(0, position_ms))}
        if device_id:
            params["device_id"] = device_id
        self._request("PUT", Endpoints.PLAYER_SEEK, params=params)

    def set_repeat(self, mode: str, device_id: str | None = None) -> None:
        params = {"state": mode}
        if device_id:
            params["device_id"] = device_id
        self._request("PUT", Endpoints.PLAYER_REPEAT, params=params)

    def set_shuffle(self, enabled: bool, device_id: str | None = None) -> None:
        params = {"state": "true" if enabled else "false"}
        if device_id:
            params["device_id"] = device_id
        self._request("PUT", Endpoints.PLAYER_SHUFFLE, params=params)

    def set_volume(self, percent: int, device_id: str | None = None) -> None:
        params = {"volume_percent": int(max(0, min(100, percent)))}
        if device_id:
            params["device_id"] = device_id
        self._request("PUT", Endpoints.PLAYER_VOLUME, params=params)

    def transfer_playback(self, device_id: str, play: bool = False) -> None:
        self._request("PUT", Endpoints.PLAYER, json_body={"device_ids": [device_id], "play": bool(play)})

    # -- library ----------------------------------------------------------

    def library_contains(self, uris: list[str]) -> list[bool]:
        if not uris:
            return []
        payload = self._request("GET", Endpoints.LIBRARY_CONTAINS, params={"uris": ",".join(uris)})
        if isinstance(payload, list):
            return [bool(value) for value in payload]
        if isinstance(payload, dict):
            # Tolerates a keyed response shape as well as a bare array.
            return [bool(payload.get(uri)) for uri in uris]
        return []

    def library_save(self, uris: list[str]) -> None:
        # `uris` goes in the query string, exactly as it does for the contains
        # check. Sent as a JSON body instead, Spotify answers 400 "Missing
        # required field: uris" — it does not read the body on this route.
        if not uris:
            return
        self._request("PUT", Endpoints.LIBRARY, params={"uris": ",".join(uris)})

    def library_remove(self, uris: list[str]) -> None:
        if not uris:
            return
        self._request("DELETE", Endpoints.LIBRARY, params={"uris": ",".join(uris)})

    def get_saved_tracks(self, limit: int = MAX_PAGE_SIZE, offset: int = 0) -> dict:
        return self._request(
            "GET",
            Endpoints.SAVED_TRACKS,
            params={"limit": min(MAX_PAGE_SIZE, max(1, limit)), "offset": max(0, offset)},
        ) or {}

    # -- playlists --------------------------------------------------------

    def get_playlists(self, limit: int = MAX_PAGE_SIZE, offset: int = 0) -> dict:
        return self._request(
            "GET",
            Endpoints.PLAYLISTS,
            params={"limit": min(MAX_PAGE_SIZE, max(1, limit)), "offset": max(0, offset)},
        ) or {}

    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        self._request("POST", Endpoints.playlist_items(playlist_id), json_body={"uris": list(uris)})

    # -- context ----------------------------------------------------------

    def get_context(self, uri: str) -> dict | None:
        """Resolve a playback context URI to its metadata, once per URI."""
        from .uri import parse_resource

        resource = parse_resource(uri)
        if resource is None:
            return None

        path_for_type = {
            "playlist": Endpoints.playlist,
            "album": Endpoints.album,
            "artist": Endpoints.artist,
            "show": Endpoints.show,
        }.get(resource.resource_type)

        if path_for_type is None:
            return None

        try:
            return self._request("GET", path_for_type(resource.resource_id))
        except SpotifyApiError as error:
            log.info(f"Spotify: could not resolve context ({error})")
            return None


def _payload(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_message(response) -> str | None:
    error = _payload(response).get("error")
    if isinstance(error, dict):
        return error.get("message")
    if isinstance(error, str):
        return error
    return None


def _error_reason(response) -> str | None:
    error = _payload(response).get("error")
    if isinstance(error, dict):
        return error.get("reason")
    return None


def _looks_like_no_device(response) -> bool:
    message = (_error_message(response) or "").lower()
    return "no active device" in message or "device not found" in message


def _retry_after_seconds(response) -> float:
    """Spotify's Retry-After, with a floor so a missing header cannot mean 0."""
    header = response.headers.get("Retry-After") if getattr(response, "headers", None) else None
    try:
        return max(1.0, float(header))
    except (TypeError, ValueError):
        return 5.0
