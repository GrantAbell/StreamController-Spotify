"""Every endpoint this plugin calls, and how each failure is translated.

The path assertions here are the guard rail for Spotify's 2026 endpoint moves:
if someone reintroduces a deprecated route, these fail rather than the user
finding out when Like silently stops working.
"""

from __future__ import annotations

import inspect

import pytest
import requests

from spotify_essentials.spotify import api as api_module
from spotify_essentials.spotify.api import SpotifyApiClient
from spotify_essentials.spotify.errors import (
    SpotifyApiError,
    SpotifyAuthError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyRateLimitError,
    SpotifyRestrictedError,
    SpotifyShutdownError,
)

BASE = "https://api.spotify.com/v1"


class _Response:
    def __init__(self, status=200, payload=None, headers=None, content=b"{}"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.content = content if payload is None else b'{"x": 1}'

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests: list[dict] = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.requests.append(
            {"method": method, "url": url, "params": params, "json": json, "headers": headers, "timeout": timeout}
        )
        if not self.responses:
            return _Response(200, {"ok": True})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def last(self) -> dict:
        return self.requests[-1]

    def path_of(self, index=-1) -> str:
        return self.requests[index]["url"].replace(BASE, "")


class _Auth:
    def __init__(self):
        self.tokens = ["token-1", "token-2"]
        self.refreshes = 0
        self.invalidated = False

    def get_access_token(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
        return self.tokens[min(self.refreshes, len(self.tokens) - 1)]

    def mark_invalid(self):
        self.invalidated = True


def _client(responses=None):
    auth = _Auth()
    session = _Session(responses)
    return SpotifyApiClient(auth, session=session), session, auth


# -- paths -----------------------------------------------------------------


def test_player_endpoints():
    client, session, _ = _client()

    client.get_playback_state()
    assert (session.last["method"], session.path_of()) == ("GET", "/me/player")
    assert session.last["params"]["additional_types"] == "track,episode"

    client.get_devices()
    assert (session.last["method"], session.path_of()) == ("GET", "/me/player/devices")

    client.play()
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/play")
    # An empty body means "resume", which is not the same as starting something.
    assert session.last["json"] is None

    client.pause()
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/pause")

    client.next_track()
    assert (session.last["method"], session.path_of()) == ("POST", "/me/player/next")

    client.previous_track()
    assert (session.last["method"], session.path_of()) == ("POST", "/me/player/previous")

    client.seek(42000)
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/seek")
    assert session.last["params"]["position_ms"] == 42000

    client.set_repeat("context")
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/repeat")
    assert session.last["params"]["state"] == "context"

    client.set_shuffle(True)
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/shuffle")
    assert session.last["params"]["state"] == "true"

    client.set_volume(65)
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player/volume")
    assert session.last["params"]["volume_percent"] == 65

    client.transfer_playback("device-9", play=True)
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/player")
    assert session.last["json"] == {"device_ids": ["device-9"], "play": True}


def test_library_uses_the_generic_endpoints():
    client, session, _ = _client([_Response(200, [True])])

    client.library_contains(["spotify:track:abc"])
    assert (session.last["method"], session.path_of()) == ("GET", "/me/library/contains")
    assert session.last["params"] == {"uris": "spotify:track:abc"}

    # Saving and removing take the URIs as query parameters, like the contains
    # check does. Sent as a JSON body, Spotify answers "Missing required field:
    # uris" — it does not read the body on this route.
    client.library_save(["spotify:track:abc", "spotify:track:def"])
    assert (session.last["method"], session.path_of()) == ("PUT", "/me/library")
    assert session.last["params"] == {"uris": "spotify:track:abc,spotify:track:def"}
    assert session.last["json"] is None

    client.library_remove(["spotify:track:abc"])
    assert (session.last["method"], session.path_of()) == ("DELETE", "/me/library")
    assert session.last["params"] == {"uris": "spotify:track:abc"}
    assert session.last["json"] is None


def test_empty_library_changes_never_become_requests():
    client, session, _ = _client()
    client.library_save([])
    client.library_remove([])
    assert session.requests == []


def test_saved_tracks_and_playlists_paginate():
    client, session, _ = _client()

    client.get_saved_tracks(limit=50, offset=100)
    assert (session.last["method"], session.path_of()) == ("GET", "/me/tracks")
    assert session.last["params"] == {"limit": 50, "offset": 100}

    client.get_playlists(limit=50, offset=0)
    assert (session.last["method"], session.path_of()) == ("GET", "/me/playlists")


def test_playlist_additions_use_the_items_route():
    client, session, _ = _client()
    client.add_to_playlist("pl1", ["spotify:track:abc"])

    assert (session.last["method"], session.path_of()) == ("POST", "/playlists/pl1/items")
    assert session.last["json"] == {"uris": ["spotify:track:abc"]}


def test_context_resolution_dispatches_on_the_uri_type():
    client, session, _ = _client()

    client.get_context("spotify:playlist:pl1")
    assert session.path_of() == "/playlists/pl1"

    client.get_context("spotify:album:al1")
    assert session.path_of() == "/albums/al1"

    client.get_context("spotify:artist:ar1")
    assert session.path_of() == "/artists/ar1"

    client.get_context("spotify:show:sh1")
    assert session.path_of() == "/shows/sh1"

    before = len(session.requests)
    assert client.get_context("not a uri") is None
    assert len(session.requests) == before, "an unusable URI must not become a request"


def test_deprecated_routes_are_absent_from_the_source():
    """Spotify moved these in 2026; the old forms must never come back."""
    endpoints = api_module.Endpoints
    paths = {value for name, value in vars(endpoints).items() if isinstance(value, str) and value.startswith("/")}
    paths |= {
        endpoints.playlist_items("PL"),
        endpoints.playlist("PL"),
        endpoints.album("AL"),
        endpoints.artist("AR"),
        endpoints.show("SH"),
    }

    for path in paths:
        assert path != "/me/tracks/contains", "library checks moved to /me/library/contains"
        assert not path.endswith("/tracks") or path == "/me/tracks", f"deprecated route: {path}"

    source = inspect.getsource(api_module)
    # /me/tracks survives only as the read endpoint for saved tracks…
    assert source.count('"/me/tracks"') == 1
    # …and is only ever read from.
    users = [
        name
        for name, member in vars(SpotifyApiClient).items()
        if callable(member) and "SAVED_TRACKS" in inspect.getsource(member)
    ]
    assert users == ["get_saved_tracks"], f"saved tracks must only be read, used by: {users}"
    assert '"GET"' in inspect.getsource(SpotifyApiClient.get_saved_tracks)


def test_page_sizes_are_capped_at_the_api_maximum():
    client, session, _ = _client()
    client.get_saved_tracks(limit=500)
    assert session.last["params"]["limit"] == 50


def test_volume_is_clamped_before_it_is_sent():
    client, session, _ = _client()
    client.set_volume(500)
    assert session.last["params"]["volume_percent"] == 100
    client.set_volume(-20)
    assert session.last["params"]["volume_percent"] == 0


def test_the_token_goes_in_the_authorization_header():
    client, session, _ = _client()
    client.get_playback_state()
    assert session.last["headers"]["Authorization"] == "Bearer token-1"


def test_every_request_has_a_finite_timeout():
    client, session, _ = _client()
    client.get_playback_state()
    connect, read = session.last["timeout"]
    assert connect > 0 and read > 0


# -- failures --------------------------------------------------------------


def test_204_means_nothing_is_playing():
    client, _, _ = _client([_Response(204, None, content=b"")])
    assert client.get_playback_state() is None


def test_401_refreshes_once_and_retries():
    client, session, auth = _client([_Response(401, {"error": {"message": "expired"}}), _Response(200, {"ok": True})])

    assert client.get_playback_state() == {"ok": True}
    assert auth.refreshes == 1
    assert len(session.requests) == 2
    assert session.requests[1]["headers"]["Authorization"] == "Bearer token-2"
    assert not auth.invalidated


def test_a_second_401_gives_up_instead_of_looping():
    client, session, auth = _client([_Response(401, {}), _Response(401, {})])

    with pytest.raises(SpotifyAuthError):
        client.get_playback_state()

    assert auth.invalidated
    assert len(session.requests) == 2, "exactly one retry, never a refresh loop"


def test_429_reports_the_retry_after_header():
    client, _, _ = _client([_Response(429, {}, headers={"Retry-After": "37"})])

    with pytest.raises(SpotifyRateLimitError) as error:
        client.next_track()
    assert error.value.retry_after == 37


def test_429_without_a_header_still_backs_off():
    client, _, _ = _client([_Response(429, {})])

    with pytest.raises(SpotifyRateLimitError) as error:
        client.next_track()
    assert error.value.retry_after >= 1


def test_403_is_a_restriction_not_a_crash():
    client, _, _ = _client([_Response(403, {"error": {"message": "Player command failed: Restriction violated"}})])

    with pytest.raises(SpotifyRestrictedError):
        client.next_track()


def test_404_with_no_active_device_is_its_own_condition():
    client, _, _ = _client([_Response(404, {"error": {"reason": "NO_ACTIVE_DEVICE", "message": "no device"}})])

    with pytest.raises(SpotifyNoDeviceError):
        client.pause()


def test_other_404s_stay_api_errors():
    client, _, _ = _client([_Response(404, {"error": {"message": "Non existing id"}})])

    with pytest.raises(SpotifyApiError) as error:
        client.pause()
    assert error.value.status_code == 404


def test_500_is_an_api_error():
    client, _, _ = _client([_Response(500, {"error": {"message": "oops"}})])

    with pytest.raises(SpotifyApiError) as error:
        client.get_devices()
    assert error.value.status_code == 500


def test_timeouts_and_connection_failures_become_network_errors():
    client, _, _ = _client([requests.Timeout("slow")])
    with pytest.raises(SpotifyNetworkError):
        client.get_playback_state()

    client, _, _ = _client([requests.ConnectionError("down")])
    with pytest.raises(SpotifyNetworkError):
        client.get_playback_state()


def test_no_requests_start_after_close():
    client, session, _ = _client()
    client.close()

    with pytest.raises(SpotifyShutdownError):
        client.get_playback_state()
    assert session.requests == []


def test_library_contains_tolerates_either_response_shape():
    client, _, _ = _client([_Response(200, {"spotify:track:a": True, "spotify:track:b": False})])
    assert client.library_contains(["spotify:track:a", "spotify:track:b"]) == [True, False]

    client, session, _ = _client()
    assert client.library_contains([]) == []
    assert session.requests == [], "an empty check must not become a request"
