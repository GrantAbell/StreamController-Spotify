#!/usr/bin/env python3
"""Prove the Spotify half of the plugin works in this environment, standalone.

Run it the way StreamController will run the plugin — inside the Flatpak — so
that HTTPS, the 127.0.0.1 callback, browser launch and token persistence are all
verified in the sandbox rather than in a friendlier shell:

    flatpak run --command=python3 com.core447.StreamController \\
        ~/repos/StreamController-Spotify/spotify_probe.py --client-id <ID>

It authenticates, reads your profile and playback state, toggles playback,
forces a token refresh, and exits cleanly. It touches nothing StreamController
owns except an optional token file you point it at.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_package():
    """Load this directory as a package so its relative imports resolve."""
    spec = importlib.util.spec_from_file_location(
        "spotify_essentials", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["spotify_essentials"] = module
    spec.loader.exec_module(module)


_load_package()

import requests  # noqa: E402

from spotify_essentials.spotify.api import SpotifyApiClient  # noqa: E402
from spotify_essentials.spotify.auth import (  # noqa: E402
    DEFAULT_CALLBACK_PORT,
    SpotifyAuthManager,
    TokenStore,
    redirect_uri_for_port,
)
from spotify_essentials.spotify.errors import SpotifyPluginError  # noqa: E402
from spotify_essentials.spotify.models import parse_devices, parse_playback, parse_profile  # noqa: E402
from spotify_essentials.spotify.scopes import missing_scopes  # noqa: E402
from spotify_essentials.spotify.state import format_duration  # noqa: E402

OK = "  ok  "
FAIL = " FAIL "


def step(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{OK if ok else FAIL}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", required=True, help="Your Spotify app's Client ID")
    parser.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT, help="Loopback callback port")
    parser.add_argument("--token-file", default=str(ROOT / ".probe-auth.json"), help="Where to keep the tokens")
    parser.add_argument("--no-toggle", action="store_true", help="Skip the play/pause test")
    arguments = parser.parse_args()

    print(f"Redirect URI to register: {redirect_uri_for_port(arguments.port)}\n")

    session = requests.Session()
    store = TokenStore(arguments.token_file)
    auth = SpotifyAuthManager(
        store,
        session,
        client_id_provider=lambda: arguments.client_id,
        port_provider=lambda: arguments.port,
    )
    api = SpotifyApiClient(auth, session=session)
    failures = 0

    try:
        # 1-5: authenticate, unless a usable grant is already stored.
        if auth.is_authenticated:
            print("Using the tokens already in the token file.")
        else:
            auth.authenticate()
            deadline = time.monotonic() + 300
            while auth.is_authenticating and time.monotonic() < deadline:
                time.sleep(0.25)

        if not step("authenticated", auth.is_authenticated, auth.last_error or ""):
            return 1

        missing = missing_scopes(auth.granted_scopes)
        step("all required scopes granted", not missing, ", ".join(missing))

        # 6: profile.
        profile = parse_profile(api.get_profile())
        failures += not step(
            "profile",
            profile is not None,
            f"{profile.display_name} ({'Premium' if profile.is_premium else 'not Premium'})" if profile else "",
        )
        if profile and profile.is_premium is False:
            print("      note: Spotify refuses playback control for non-Premium accounts")

        # 7: devices and playback.
        devices = parse_devices(api.get_devices())
        step("devices", True, ", ".join(f"{device.name}" for device in devices) or "none")

        state = parse_playback(api.get_playback_state())
        if state.has_playback and state.track:
            detail = (
                f"{state.track.name} — {state.track.artist_text} "
                f"[{format_duration(state.progress_ms)}/{format_duration(state.duration_ms)}]"
            )
        else:
            detail = "nothing playing"
        step("playback state", True, detail)

        # 8: a real command.
        if arguments.no_toggle:
            print("[ skip ] play/pause toggle")
        elif not state.has_playback:
            print("[ skip ] play/pause toggle — start something in Spotify first")
        else:
            was_playing = state.is_playing
            if was_playing:
                api.pause()
            else:
                api.play()
            time.sleep(1.5)

            after = parse_playback(api.get_playback_state())
            failures += not step("play/pause toggled", after.is_playing != was_playing)

            # Put it back the way it was.
            if after.is_playing != was_playing:
                api.play() if was_playing else api.pause()

        # 9: refresh.
        before = auth.get_access_token()
        after = auth.get_access_token(force_refresh=True)
        failures += not step("token refresh", bool(after), "new access token issued" if after != before else "reused")
        failures += not step("refresh token retained", bool(store.load().refresh_token))

    except SpotifyPluginError as error:
        step(f"{error.__class__.__name__}", False, str(error))
        failures += 1
    finally:
        # 10: exit cleanly.
        auth.shutdown()
        api.close()
        session.close()

    print()
    print("All checks passed." if not failures else f"{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
