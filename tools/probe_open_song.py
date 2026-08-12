"""Drive the real open-in-app path against the running Spotify client.

Run it the way the plugin runs, inside the Flatpak, so the D-Bus route and the
Spotify calls are exercised exactly as StreamController would:

    flatpak run --command=python3 com.core447.StreamController \\
        ~/repos/StreamController-Spotify/tools/probe_open_song.py

StreamController must not be running: this borrows its token file, and the two
would otherwise refresh it against each other.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "spotify_essentials", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
module = importlib.util.module_from_spec(spec)
sys.modules["spotify_essentials"] = module
spec.loader.exec_module(module)

from spotify_essentials.spotify.manager import SpotifyManager  # noqa: E402

SETTINGS_DIR = Path.home() / ".var/app/com.core447.StreamController/data/settings/plugins/com_grantabell_Spotify"
CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "6f7cf9b044534b6c810786d8296240c6")


def mpris(name: str):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(
        "org.mpris.MediaPlayer2.spotify",
        "/org/mpris/MediaPlayer2",
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", name)),
        None,
        Gio.DBusCallFlags.NO_AUTO_START,
        1500,
        None,
    ).unpack()[0]


def sample(label: str) -> None:
    print(
        f"{label:>6}  {mpris('PlaybackStatus')}  {mpris('Position') / 1_000_000:>6.1f}s  "
        f"{mpris('Metadata').get('xesam:title')}"
    )


manager = SpotifyManager(
    token_path=str(SETTINGS_DIR / "spotify-auth.json"),
    settings_provider=lambda: {"spotify_client_id": CLIENT_ID},
    auto_start=False,
)

try:
    manager._poll_playback()
    state = manager.get_playback_state()
    print(f"playing : {state.track.name if state.track else None} at {state.progress_ms}ms")
    print(f"context : {state.context_uri}")
    print(f"restore : {manager._restore_point()}")

    target = sys.argv[1] if len(sys.argv) > 1 else (state.track.external_url or state.track.uri)
    print(f"target  : {target}")

    sample("before")
    print(f">>> open_in_spotify -> {manager.open_in_spotify(target)}")

    for _ in range(7):
        time.sleep(3)
        sample("after")
finally:
    manager.shutdown()
