"""OAuth Authorization Code with PKCE, and where the resulting tokens live.

No client secret is involved anywhere in this file. The user supplies only a
Client ID, which is not secret; the proof of possession is the PKCE verifier,
which is generated per attempt and never written to disk or to a log.

The callback listener binds to 127.0.0.1 only. Spotify permits plain HTTP for a
loopback IP literal but not for the name `localhost`, so the literal is used
everywhere, including in the redirect URI the user registers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .errors import SpotifyAuthError, SpotifyNetworkError
from .log import log
from .scopes import SCOPE_STRING

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

DEFAULT_CALLBACK_PORT = 8888
CALLBACK_PATH = "/callback"

#: How long the browser has to complete the flow before the listener gives up.
CALLBACK_TIMEOUT_SECONDS = 300.0

#: Refresh this long before the token actually expires, so a command issued at
#: the wrong moment does not have to pay for a 401 and a retry.
REFRESH_MARGIN_SECONDS = 60.0

_HTTP_TIMEOUT = (3.05, 10)


def redirect_uri_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


@dataclass
class PkceChallenge:
    verifier: str
    challenge: str
    state: str

    @staticmethod
    def generate() -> "PkceChallenge":
        # 96 random bytes -> 128 base64url characters, the maximum length RFC
        # 7636 allows for a verifier.
        verifier = _b64url(secrets.token_bytes(96))
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return PkceChallenge(
            verifier=verifier,
            challenge=_b64url(digest),
            state=_b64url(secrets.token_bytes(24)),
        )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_authorization_url(client_id: str, redirect_uri: str, challenge: PkceChallenge) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge.challenge,
            "state": challenge.state,
            "scope": SCOPE_STRING,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


@dataclass
class TokenSet:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    granted_scopes: list[str] = field(default_factory=list)

    @property
    def is_present(self) -> bool:
        return bool(self.refresh_token or self.access_token)

    def is_fresh(self, now: float | None = None, margin: float = REFRESH_MARGIN_SECONDS) -> bool:
        now = time.time() if now is None else now
        return bool(self.access_token) and self.expires_at - margin > now

    def to_json(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "granted_scopes": list(self.granted_scopes),
        }

    @staticmethod
    def from_json(data: dict | None) -> "TokenSet":
        data = data or {}
        try:
            expires_at = float(data.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            expires_at = 0.0
        scopes = data.get("granted_scopes")
        return TokenSet(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=expires_at,
            granted_scopes=list(scopes) if isinstance(scopes, list) else [],
        )


class TokenStore:
    """The token file: user-only permissions, outside any page settings.

    Page settings are exported and duplicated along with a page, so a token
    stored there would travel with any page a user shared. This file does not.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> TokenSet:
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    return TokenSet.from_json(json.load(handle))
            except FileNotFoundError:
                return TokenSet()
            except (json.JSONDecodeError, OSError) as error:
                log.warning(f"Spotify: could not read stored credentials ({error.__class__.__name__})")
                return TokenSet()

    def save(self, tokens: TokenSet) -> None:
        with self._lock:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)

            temporary = f"{self.path}.tmp"
            # Created 0600 before anything is written, so the tokens are never
            # briefly world-readable.
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(tokens.to_json(), handle)
            except Exception:
                os.unlink(temporary)
                raise
            os.replace(temporary, self.path)

    def clear(self) -> None:
        with self._lock:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            except OSError as error:
                log.warning(f"Spotify: could not remove stored credentials ({error.__class__.__name__})")


class _CallbackHandler(BaseHTTPRequestHandler):
    """Answers the single browser redirect, then lets the server stop."""

    server_version = "SpotifyEssentials/1.0"

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]
        error = (params.get("error") or [None])[0]

        listener: "_CallbackListener" = self.server.listener  # type: ignore[attr-defined]
        message, ok = listener.deliver(code=code, state=state, error=error)

        body = _result_page(message, ok).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        # Silence the default stderr access log: the query string of this exact
        # request contains the authorization code.
        return


def _result_page(message: str, ok: bool) -> str:
    colour = "#1ED760" if ok else "#E2574C"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Spotify</title>
<style>
 body {{ background:#191414; color:#FFFFFF; font-family:system-ui,sans-serif;
        display:flex; align-items:center; justify-content:center; height:100vh; margin:0 }}
 .card {{ text-align:center; padding:2.5rem 3rem; border:1px solid #2a2a2a; border-radius:14px }}
 h1 {{ color:{colour}; font-size:1.35rem; margin:0 0 .6rem }}
 p {{ color:#b3b3b3; margin:0 }}
</style></head>
<body><div class="card"><h1>{message}</h1><p>You can close this tab and return to StreamController.</p></div></body></html>"""


@dataclass
class _CallbackResult:
    code: str | None = None
    error: str | None = None


class _CallbackListener:
    """A one-shot loopback HTTP server for the redirect."""

    def __init__(self, port: int, expected_state: str):
        self.expected_state = expected_state
        self.result = _CallbackResult()
        self._done = threading.Event()

        self._server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        self._server.listener = self  # type: ignore[attr-defined]
        self._server.timeout = 1.0
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="spotify-oauth-callback",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def deliver(self, code: str | None, state: str | None, error: str | None) -> tuple[str, bool]:
        if error:
            self.result = _CallbackResult(error=error)
            self._done.set()
            return ("Authorization declined", False)

        if not secrets.compare_digest(state or "", self.expected_state):
            # A mismatched state means this redirect did not come from the
            # request we made, so the code it carries is not trusted.
            self.result = _CallbackResult(error="state_mismatch")
            self._done.set()
            return ("Authorization could not be verified", False)

        if not code:
            self.result = _CallbackResult(error="missing_code")
            self._done.set()
            return ("Authorization response was incomplete", False)

        self.result = _CallbackResult(code=code)
        self._done.set()
        return ("Spotify connected", True)

    def wait(self, timeout: float) -> _CallbackResult:
        if not self._done.wait(timeout):
            return _CallbackResult(error="timeout")
        return self.result

    def stop(self) -> None:
        self._done.set()
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
        try:
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass


class SpotifyAuthManager:
    """Owns the tokens, the refresh, and one authentication attempt at a time."""

    def __init__(
        self,
        token_store: TokenStore,
        session: requests.Session,
        client_id_provider: Callable[[], str],
        port_provider: Callable[[], int] = lambda: DEFAULT_CALLBACK_PORT,
        open_url: Callable[[str], None] | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self._store = token_store
        self._session = session
        self._client_id_provider = client_id_provider
        self._port_provider = port_provider
        self._open_url = open_url
        self._on_change = on_change

        self._lock = threading.RLock()
        self._tokens = token_store.load()
        self._listener: _CallbackListener | None = None
        self._attempt: threading.Thread | None = None
        self._authenticating = False
        self._last_error: str | None = None
        self._shutdown = False

    # -- status -----------------------------------------------------------

    @property
    def client_id(self) -> str:
        return (self._client_id_provider() or "").strip()

    @property
    def is_authenticated(self) -> bool:
        with self._lock:
            return self._tokens.is_present and bool(self.client_id)

    @property
    def is_authenticating(self) -> bool:
        with self._lock:
            return self._authenticating

    @property
    def granted_scopes(self) -> list[str]:
        with self._lock:
            return list(self._tokens.granted_scopes)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def redirect_uri(self) -> str:
        return redirect_uri_for_port(self._port_provider())

    # -- authentication ---------------------------------------------------

    def authenticate(self) -> None:
        """Begin a browser authorization. Returns immediately.

        Called from a GTK button handler, so all the waiting happens on a
        worker thread and the settings window never blocks.
        """
        with self._lock:
            if self._shutdown:
                return
            if self._authenticating:
                log.info("Spotify: an authentication attempt is already running")
                return
            if not self.client_id:
                self._last_error = "Enter your Spotify Client ID first"
                self._notify()
                return
            self._authenticating = True
            self._last_error = None

        self._attempt = threading.Thread(target=self._run_attempt, name="spotify-auth", daemon=True)
        self._attempt.start()
        self._notify()

    def _run_attempt(self) -> None:
        challenge = PkceChallenge.generate()
        port = self._port_provider()
        listener = None

        try:
            try:
                listener = _CallbackListener(port, challenge.state)
            except OSError as error:
                raise SpotifyAuthError(
                    f"Could not listen on 127.0.0.1:{port} ({error.strerror or error}). "
                    "Close whatever is using that port, or choose another one in the plugin settings."
                ) from error

            with self._lock:
                self._listener = listener
            listener.start()

            redirect_uri = redirect_uri_for_port(listener.port)
            url = build_authorization_url(self.client_id, redirect_uri, challenge)
            log.info("Spotify: opening the authorization page in your browser")
            self._open(url)

            result = listener.wait(CALLBACK_TIMEOUT_SECONDS)
            if result.error:
                raise SpotifyAuthError(_describe_callback_error(result.error))

            self._exchange_code(result.code, challenge.verifier, redirect_uri)
            log.info("Spotify authentication completed")

        except SpotifyAuthError as error:
            with self._lock:
                self._last_error = str(error)
            log.error(f"Spotify authentication failed: {error}")
        except Exception as error:  # noqa: BLE001 - a worker thread must not die silently
            with self._lock:
                self._last_error = "Authentication failed unexpectedly"
            log.exception(f"Spotify authentication failed: {error.__class__.__name__}")
        finally:
            if listener is not None:
                listener.stop()
            with self._lock:
                self._listener = None
                self._authenticating = False
            self._notify()

    def cancel_authentication(self) -> None:
        with self._lock:
            listener = self._listener
        if listener is not None:
            listener.stop()

    def _open(self, url: str) -> None:
        if self._open_url is not None:
            self._open_url(url)
            return
        import webbrowser

        webbrowser.open(url)

    def _exchange_code(self, code: str, verifier: str, redirect_uri: str) -> None:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        data = self._post_token(payload)
        self._store_token_response(data, previous_refresh_token="")

    # -- tokens -----------------------------------------------------------

    def get_access_token(self, force_refresh: bool = False) -> str:
        """A usable access token, refreshing first if it is close to expiry."""
        with self._lock:
            if self._shutdown:
                raise SpotifyAuthError("Plugin is shutting down", needs_reauth=False)
            tokens = self._tokens
            if not tokens.is_present or not self.client_id:
                raise SpotifyAuthError()
            if not force_refresh and tokens.is_fresh():
                return tokens.access_token
            refresh_token = tokens.refresh_token

        if not refresh_token:
            raise SpotifyAuthError()

        return self._refresh(refresh_token)

    def _refresh(self, refresh_token: str) -> str:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        data = self._post_token(payload)
        # Spotify may or may not issue a replacement refresh token; when it does
        # not, the existing one stays valid and must be kept.
        self._store_token_response(data, previous_refresh_token=refresh_token)
        with self._lock:
            return self._tokens.access_token

    def _post_token(self, payload: dict) -> dict:
        try:
            response = self._session.post(
                TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_HTTP_TIMEOUT,
            )
        except requests.Timeout as error:
            raise SpotifyNetworkError("Spotify did not answer the token request in time") from error
        except requests.RequestException as error:
            raise SpotifyNetworkError("Could not reach Spotify to sign in") from error

        if response.status_code >= 400:
            raise SpotifyAuthError(_describe_token_error(response))

        try:
            return response.json()
        except ValueError as error:
            raise SpotifyAuthError("Spotify returned an unreadable token response") from error

    def _store_token_response(self, data: dict, previous_refresh_token: str) -> None:
        access_token = data.get("access_token")
        if not access_token:
            raise SpotifyAuthError("Spotify did not return an access token")

        try:
            expires_in = float(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600.0

        scope_text = data.get("scope") or ""
        tokens = TokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token") or previous_refresh_token,
            expires_at=time.time() + expires_in,
            granted_scopes=scope_text.split() if scope_text else [],
        )

        with self._lock:
            if not tokens.granted_scopes:
                # A refresh response often omits `scope`; keep what the original
                # grant reported rather than appearing to lose permissions.
                tokens.granted_scopes = list(self._tokens.granted_scopes)
            self._tokens = tokens
            self._last_error = None

        try:
            self._store.save(tokens)
        except OSError as error:
            log.error(f"Spotify: could not persist credentials ({error.__class__.__name__})")

        self._notify()

    def mark_invalid(self) -> None:
        """Called after a refresh-then-retry still came back 401.

        The stored grant is kept on disk so the user can see they were connected
        and choose to reauthenticate, but it is no longer treated as usable.
        """
        with self._lock:
            self._tokens = TokenSet()
            self._last_error = "Spotify rejected the stored login. Please authenticate again."
        self._notify()

    def disconnect(self) -> None:
        self.cancel_authentication()
        with self._lock:
            self._tokens = TokenSet()
            self._last_error = None
        self._store.clear()
        log.info("Spotify: disconnected")
        self._notify()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
        self.cancel_authentication()

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # noqa: BLE001
            log.exception("Spotify: auth listener raised")


def _describe_callback_error(error: str) -> str:
    if error == "access_denied":
        return "You declined the Spotify authorization request"
    if error == "state_mismatch":
        return "The Spotify response could not be verified and was ignored"
    if error == "timeout":
        return "Timed out waiting for the Spotify authorization to complete"
    if error == "missing_code":
        return "Spotify's response did not contain an authorization code"
    return f"Spotify returned an authorization error ({error})"


def _describe_token_error(response) -> str:
    """A message the user can act on, never containing the request payload."""
    description = ""
    try:
        body = response.json()
        description = body.get("error_description") or body.get("error") or ""
    except ValueError:
        description = ""

    if response.status_code == 400 and "redirect" in description.lower():
        return (
            "Spotify rejected the redirect URI. Add it exactly as shown in the plugin "
            "settings to your app's Redirect URIs and try again."
        )
    if response.status_code in (400, 401) and "client" in description.lower():
        return "Spotify rejected the Client ID. Check it in the plugin settings."
    if description:
        return f"Spotify refused the sign-in: {description}"
    return f"Spotify refused the sign-in (HTTP {response.status_code})"
