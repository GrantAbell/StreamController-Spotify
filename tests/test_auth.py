"""PKCE, the token file, and the refresh rules.

The loopback listener itself is exercised end to end: a real socket on
127.0.0.1, a real redirect, and the state check that has to reject a response
that did not come from the request we made.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from spotify_essentials.spotify.auth import (
    CALLBACK_PATH,
    PkceChallenge,
    SpotifyAuthManager,
    TokenSet,
    TokenStore,
    build_authorization_url,
    redirect_uri_for_port,
    _CallbackListener,
)
from spotify_essentials.spotify.errors import SpotifyAuthError, SpotifyNetworkError
from spotify_essentials.spotify.scopes import REQUIRED_SCOPES, missing_scopes

CLIENT_ID = "client-id-under-test"


# -- PKCE ------------------------------------------------------------------


def test_verifier_and_challenge_are_correct_and_unique():
    first = PkceChallenge.generate()
    second = PkceChallenge.generate()

    assert first.verifier != second.verifier
    assert first.state != second.state

    # RFC 7636 allows 43-128 characters, unpadded base64url.
    assert 43 <= len(first.verifier) <= 128
    assert "=" not in first.verifier

    expected = base64.urlsafe_b64encode(hashlib.sha256(first.verifier.encode()).digest()).decode().rstrip("=")
    assert first.challenge == expected


def test_authorization_url_contains_everything_spotify_needs():
    challenge = PkceChallenge.generate()
    url = build_authorization_url(CLIENT_ID, redirect_uri_for_port(8888), challenge)
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).netloc == "accounts.spotify.com"
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge.challenge]
    assert query["state"] == [challenge.state]
    assert query["redirect_uri"] == ["http://127.0.0.1:8888/callback"]

    granted = query["scope"][0].split()
    assert set(granted) == set(REQUIRED_SCOPES)
    # Asking for an email address this plugin has no use for would be worse
    # than useless: it makes the consent screen scarier than the behaviour.
    assert "user-read-email" not in granted


def test_redirect_uri_uses_the_ip_literal_not_localhost():
    # Spotify permits plain HTTP for a loopback IP, but not for `localhost`.
    assert redirect_uri_for_port(9000) == "http://127.0.0.1:9000/callback"


# -- the callback listener -------------------------------------------------


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def test_callback_success():
    challenge = PkceChallenge.generate()
    listener = _CallbackListener(0, challenge.state)
    listener.start()
    try:
        body = _get(f"http://127.0.0.1:{listener.port}{CALLBACK_PATH}?code=the-code&state={challenge.state}")
        assert "Spotify connected" in body

        result = listener.wait(2.0)
        assert result.code == "the-code"
        assert result.error is None
    finally:
        listener.stop()


def test_callback_denied():
    challenge = PkceChallenge.generate()
    listener = _CallbackListener(0, challenge.state)
    listener.start()
    try:
        _get(f"http://127.0.0.1:{listener.port}{CALLBACK_PATH}?error=access_denied&state={challenge.state}")
        assert listener.wait(2.0).error == "access_denied"
    finally:
        listener.stop()


def test_callback_with_a_forged_state_is_rejected():
    challenge = PkceChallenge.generate()
    listener = _CallbackListener(0, challenge.state)
    listener.start()
    try:
        _get(f"http://127.0.0.1:{listener.port}{CALLBACK_PATH}?code=stolen&state=not-the-state")
        result = listener.wait(2.0)

        assert result.error == "state_mismatch"
        # The code from an unverified redirect must never be used.
        assert result.code is None
    finally:
        listener.stop()


def test_callback_timeout():
    listener = _CallbackListener(0, "state")
    listener.start()
    try:
        assert listener.wait(0.05).error == "timeout"
    finally:
        listener.stop()


# -- the token file --------------------------------------------------------


def test_token_file_is_written_user_only(tmp_path):
    path = tmp_path / "nested" / "spotify-auth.json"
    store = TokenStore(str(path))
    store.save(TokenSet("access", "refresh", time.time() + 3600, ["a"]))

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"tokens must not be readable by other users, got {oct(mode)}"

    reloaded = store.load()
    assert reloaded.access_token == "access"
    assert reloaded.refresh_token == "refresh"
    assert reloaded.granted_scopes == ["a"]


def test_missing_and_corrupt_token_files_load_as_empty(tmp_path):
    assert not TokenStore(str(tmp_path / "absent.json")).load().is_present

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert not TokenStore(str(corrupt)).load().is_present


def test_clearing_removes_the_file(tmp_path):
    path = tmp_path / "spotify-auth.json"
    store = TokenStore(str(path))
    store.save(TokenSet("a", "r", time.time() + 60))
    store.clear()

    assert not path.exists()
    store.clear()  # clearing twice is not an error


def test_freshness_accounts_for_the_refresh_margin():
    now = time.time()
    assert TokenSet("a", "r", now + 3600).is_fresh(now)
    assert not TokenSet("a", "r", now + 10).is_fresh(now)
    assert not TokenSet("", "r", now + 3600).is_fresh(now)


# -- the auth manager ------------------------------------------------------


class _StubResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "data": data})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _manager(tmp_path, session, tokens: TokenSet | None = None) -> SpotifyAuthManager:
    store = TokenStore(str(tmp_path / "spotify-auth.json"))
    if tokens is not None:
        store.save(tokens)
    return SpotifyAuthManager(store, session, client_id_provider=lambda: CLIENT_ID)


def test_refresh_keeps_the_existing_refresh_token_when_none_is_returned(tmp_path):
    # Spotify often omits `refresh_token` from a refresh response; treating that
    # as "no refresh token" would silently log the user out.
    session = _StubSession([_StubResponse(200, {"access_token": "new-access", "expires_in": 3600})])
    manager = _manager(tmp_path, session, TokenSet("old-access", "keep-me", 0, list(REQUIRED_SCOPES)))

    assert manager.get_access_token() == "new-access"

    stored = TokenStore(str(tmp_path / "spotify-auth.json")).load()
    assert stored.refresh_token == "keep-me"
    assert stored.granted_scopes == list(REQUIRED_SCOPES)


def test_refresh_adopts_a_replacement_refresh_token(tmp_path):
    session = _StubSession(
        [_StubResponse(200, {"access_token": "a", "refresh_token": "rotated", "expires_in": 3600, "scope": "user-read-private"})]
    )
    manager = _manager(tmp_path, session, TokenSet("old", "original", 0))
    manager.get_access_token()

    assert TokenStore(str(tmp_path / "spotify-auth.json")).load().refresh_token == "rotated"


def test_a_fresh_token_is_reused_without_a_request(tmp_path):
    session = _StubSession([])
    manager = _manager(tmp_path, session, TokenSet("still-good", "r", time.time() + 3600))

    assert manager.get_access_token() == "still-good"
    assert session.requests == []


def test_no_stored_grant_means_authentication_is_required(tmp_path):
    manager = _manager(tmp_path, _StubSession([]))
    with pytest.raises(SpotifyAuthError):
        manager.get_access_token()


def test_a_rejected_refresh_is_reported_as_an_auth_error(tmp_path):
    session = _StubSession([_StubResponse(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})])
    manager = _manager(tmp_path, session, TokenSet("old", "revoked", 0))

    with pytest.raises(SpotifyAuthError):
        manager.get_access_token()


def test_network_failure_during_refresh_is_not_an_auth_error(tmp_path):
    import requests

    session = _StubSession([requests.ConnectionError("no route")])
    manager = _manager(tmp_path, session, TokenSet("old", "r", 0))

    # Being offline is not the same as being logged out, and must not make the
    # plugin throw the grant away.
    with pytest.raises(SpotifyNetworkError):
        manager.get_access_token()


def test_marking_invalid_stops_the_manager_using_the_grant(tmp_path):
    manager = _manager(tmp_path, _StubSession([]), TokenSet("a", "r", time.time() + 3600))
    assert manager.is_authenticated

    manager.mark_invalid()
    assert not manager.is_authenticated
    assert manager.last_error


def test_disconnect_clears_the_stored_tokens(tmp_path):
    path = tmp_path / "spotify-auth.json"
    manager = _manager(tmp_path, _StubSession([]), TokenSet("a", "r", time.time() + 3600))

    manager.disconnect()
    assert not manager.is_authenticated
    assert not path.exists()


def test_authentication_refuses_to_start_without_a_client_id(tmp_path):
    store = TokenStore(str(tmp_path / "spotify-auth.json"))
    manager = SpotifyAuthManager(store, _StubSession([]), client_id_provider=lambda: "")

    manager.authenticate()
    assert not manager.is_authenticating
    assert "Client ID" in (manager.last_error or "")


def test_authentication_end_to_end_against_the_real_listener(tmp_path):
    """Browser launch, redirect, state check and token exchange."""
    session = _StubSession(
        [_StubResponse(200, {"access_token": "fresh", "refresh_token": "r", "expires_in": 3600, "scope": " ".join(REQUIRED_SCOPES)})]
    )
    store = TokenStore(str(tmp_path / "spotify-auth.json"))

    opened: list[str] = []
    finished = threading.Event()

    def open_url(url: str) -> None:
        opened.append(url)
        # Stand in for the user authorising in their browser.
        query = parse_qs(urlparse(url).query)
        threading.Thread(
            target=lambda: _get(
                f"{query['redirect_uri'][0]}?code=auth-code&state={query['state'][0]}"
            ),
            daemon=True,
        ).start()

    manager = SpotifyAuthManager(
        store,
        session,
        client_id_provider=lambda: CLIENT_ID,
        port_provider=lambda: 0,
        open_url=open_url,
        on_change=lambda: finished.set() if store.load().is_present else None,
    )

    manager.authenticate()
    deadline = time.monotonic() + 5
    while manager.is_authenticating and time.monotonic() < deadline:
        time.sleep(0.02)

    assert opened, "the browser should have been asked to open the authorization page"
    assert manager.is_authenticated
    assert manager.last_error is None
    assert not missing_scopes(manager.granted_scopes)

    # The exchange must send the verifier and must never send a secret.
    body = session.requests[0]["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code"
    assert "code_verifier" in body
    assert "client_secret" not in body


def test_stored_file_shape(tmp_path):
    path = tmp_path / "spotify-auth.json"
    TokenStore(str(path)).save(TokenSet("a", "r", 123.0, ["one"]))

    assert json.loads(path.read_text()) == {
        "access_token": "a",
        "refresh_token": "r",
        "expires_at": 123.0,
        "granted_scopes": ["one"],
    }
