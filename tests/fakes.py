"""A Spotify Web API stand-in, plus the fixtures the tests drive it with.

It records every call as (method, path, params/body) so a test can assert on the
exact endpoint used — which is what stops the deprecated library and playlist
routes from creeping back in.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from spotify_essentials.spotify.api import Endpoints
from spotify_essentials.spotify.errors import (
    SpotifyApiError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyRateLimitError,
)


@dataclass
class Call:
    method: str
    path: str
    detail: dict = field(default_factory=dict)


def track_payload(
    track_id: str = "4cOdK2wGLETKBW3PvgPWqT",
    name: str = "Blinding Lights",
    artists=("The Weeknd",),
    duration_ms: int = 200040,
    explicit: bool = False,
    with_artwork: bool = True,
    is_local: bool = False,
) -> dict:
    return {
        "id": track_id,
        "type": "track",
        "uri": f"spotify:track:{track_id}",
        "name": name,
        "duration_ms": duration_ms,
        "explicit": explicit,
        "is_local": is_local,
        "artists": [{"name": artist} for artist in artists],
        "album": {
            "name": "After Hours",
            "album_type": "album",
            "images": [{"url": "https://i.example/large.jpg", "width": 640}, {"url": "https://i.example/mid.jpg", "width": 300}]
            if with_artwork
            else [],
        },
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
    }


def episode_payload() -> dict:
    return {
        "id": "512ojhOuo1ktJprKbVcKyQ",
        "type": "episode",
        "uri": "spotify:episode:512ojhOuo1ktJprKbVcKyQ",
        "name": "The One About Testing",
        "duration_ms": 3600000,
        "explicit": True,
        "images": [{"url": "https://i.example/show.jpg", "width": 300}],
        "show": {"name": "A Podcast", "publisher": "Someone"},
        "external_urls": {"spotify": "https://open.spotify.com/episode/512ojhOuo1ktJprKbVcKyQ"},
    }


def device_payload(
    device_id: str = "device-1",
    name: str = "Desktop",
    is_active: bool = True,
    volume: int | None = 50,
    supports_volume: bool = True,
    is_restricted: bool = False,
) -> dict:
    return {
        "id": device_id,
        "name": name,
        "type": "Computer",
        "is_active": is_active,
        "is_restricted": is_restricted,
        "volume_percent": volume,
        "supports_volume": supports_volume,
    }


#: Distinguishes "use the default track" from "there is deliberately no item".
UNSET = object()


def playback_payload(
    item=UNSET,
    is_playing: bool = True,
    progress_ms: int = 42000,
    shuffle: bool = False,
    smart_shuffle: bool | None = None,
    repeat: str = "off",
    device: dict | None = None,
    context_uri: str | None = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
) -> dict:
    resolved_item = track_payload() if item is UNSET else item

    # `smart_shuffle` is undocumented and simply absent for accounts and clients
    # that do not have the feature, so None means "no such key", not False.
    extra = {} if smart_shuffle is None else {"smart_shuffle": smart_shuffle}

    return {
        **extra,
        "device": device if device is not None else device_payload(),
        "repeat_state": repeat,
        "shuffle_state": shuffle,
        "context": {"uri": context_uri, "type": "playlist"} if context_uri else None,
        "timestamp": 1700000000000,
        "progress_ms": progress_ms,
        "is_playing": is_playing,
        "item": resolved_item,
        "currently_playing_type": (resolved_item or {}).get("type", "track") if resolved_item else None,
        "actions": {"disallows": {"resuming": is_playing}},
    }


class FakeSpotifyApi:
    """Implements SpotifyApiProtocol against in-memory state."""

    def __init__(
        self,
        playback: dict | None = None,
        devices: list[dict] | None = None,
        saved_tracks: list[dict] | None = None,
        playlists: list[dict] | None = None,
        liked: set[str] | None = None,
        profile: dict | None = None,
    ):
        self.playback = playback if playback is not None else playback_payload()
        self.devices = devices if devices is not None else [device_payload()]
        self.saved_tracks = saved_tracks or []
        #: Tracks a playlist or album listing returns, and the queue.
        self.context_tracks: list[dict] = []
        self.queue: list[dict] = []
        self.playlists = playlists or []
        self.liked = set(liked or ())
        self.profile = profile or {
            "id": "account-1",
            "display_name": "Test Listener",
            "product": "premium",
            "images": [{"url": "https://i.example/me.jpg", "width": 160}],
            "external_urls": {"spotify": "https://open.spotify.com/user/account-1"},
        }

        self.calls: list[Call] = []
        #: URIs handed to add_to_queue, in order.
        self.queued: list[str] = []
        self.closed = False
        #: Set to an exception instance to make the next call raise it.
        self.raise_next: Exception | None = None
        self._lock = threading.Lock()

    # -- helpers ----------------------------------------------------------

    def _record(self, method: str, path: str, **detail) -> None:
        with self._lock:
            self.calls.append(Call(method, path, detail))
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error

    def paths(self) -> list[str]:
        return [call.path for call in self.calls]

    def calls_to(self, path: str) -> list[Call]:
        return [call for call in self.calls if call.path == path]

    def close(self) -> None:
        self.closed = True

    # -- playback ---------------------------------------------------------

    def get_playback_state(self):
        self._record("GET", Endpoints.PLAYER)
        return self.playback

    def get_devices(self):
        self._record("GET", Endpoints.PLAYER_DEVICES)
        return {"devices": self.devices}

    def get_profile(self):
        self._record("GET", Endpoints.ME)
        return self.profile

    def play(self, device_id=None, context_uri=None, uris=None, position_ms=None, offset=None):
        self._record(
            "PUT",
            Endpoints.PLAYER_PLAY,
            device_id=device_id,
            context_uri=context_uri,
            uris=uris,
            offset=offset,
            position_ms=position_ms,
        )
        if self.playback:
            self.playback["is_playing"] = True

    def pause(self, device_id=None):
        self._record("PUT", Endpoints.PLAYER_PAUSE, device_id=device_id)
        if self.playback:
            self.playback["is_playing"] = False

    def next_track(self, device_id=None):
        self._record("POST", Endpoints.PLAYER_NEXT, device_id=device_id)

    def previous_track(self, device_id=None):
        self._record("POST", Endpoints.PLAYER_PREVIOUS, device_id=device_id)

    def seek(self, position_ms, device_id=None):
        self._record("PUT", Endpoints.PLAYER_SEEK, position_ms=position_ms, device_id=device_id)
        if self.playback:
            self.playback["progress_ms"] = position_ms

    def set_repeat(self, mode, device_id=None):
        self._record("PUT", Endpoints.PLAYER_REPEAT, state=mode, device_id=device_id)
        if self.playback:
            self.playback["repeat_state"] = mode

    def set_shuffle(self, enabled, device_id=None):
        self._record("PUT", Endpoints.PLAYER_SHUFFLE, state=enabled, device_id=device_id)
        if self.playback:
            self.playback["shuffle_state"] = bool(enabled)

    def add_to_queue(self, uri, device_id=None):
        self._record("POST", Endpoints.PLAYER_QUEUE, uri=uri, device_id=device_id)
        self.queued.append(uri)

    def set_volume(self, percent, device_id=None):
        self._record("PUT", Endpoints.PLAYER_VOLUME, volume_percent=percent, device_id=device_id)
        if self.playback and self.playback.get("device"):
            self.playback["device"]["volume_percent"] = percent
        for device in self.devices:
            if device_id in (None, device["id"]) and device.get("is_active", False):
                device["volume_percent"] = percent

    def transfer_playback(self, device_id, play=False):
        self._record("PUT", Endpoints.PLAYER, device_ids=[device_id], play=play)
        for device in self.devices:
            device["is_active"] = device["id"] == device_id

    # -- library ----------------------------------------------------------

    def library_contains(self, uris):
        self._record("GET", Endpoints.LIBRARY_CONTAINS, uris=list(uris))
        return [uri in self.liked for uri in uris]

    def library_save(self, uris):
        if not uris:
            return
        self._record("PUT", Endpoints.LIBRARY, uris=list(uris))
        self.liked.update(uris)

    def library_remove(self, uris):
        if not uris:
            return
        self._record("DELETE", Endpoints.LIBRARY, uris=list(uris))
        self.liked.difference_update(uris)

    def get_saved_tracks(self, limit=50, offset=0):
        self._record("GET", Endpoints.SAVED_TRACKS, limit=limit, offset=offset)
        page = self.saved_tracks[offset : offset + limit]
        return {
            "items": [{"added_at": "2026-01-01T00:00:00Z", "track": track} for track in page],
            "total": len(self.saved_tracks),
            "limit": limit,
            "offset": offset,
            "next": "more" if offset + limit < len(self.saved_tracks) else None,
        }

    # -- playlists --------------------------------------------------------

    def get_playlists(self, limit=50, offset=0):
        self._record("GET", Endpoints.PLAYLISTS, limit=limit, offset=offset)
        page = self.playlists[offset : offset + limit]
        return {
            "items": page,
            "total": len(self.playlists),
            "limit": limit,
            "offset": offset,
            "next": "more" if offset + limit < len(self.playlists) else None,
        }

    def add_to_playlist(self, playlist_id, uris):
        self._record("POST", Endpoints.playlist_items(playlist_id), uris=list(uris))

    def get_playlist_items(self, playlist_id, limit=50, offset=0):
        self._record("GET", Endpoints.playlist_items(playlist_id), limit=limit, offset=offset)
        page = self.context_tracks[offset : offset + limit]
        return {
            "items": [{"added_at": "2026-01-01T00:00:00Z", "track": track} for track in page],
            "total": len(self.context_tracks),
            "limit": limit,
            "offset": offset,
        }

    def get_album_tracks(self, album_id, limit=50, offset=0):
        self._record("GET", Endpoints.album_tracks(album_id), limit=limit, offset=offset)
        # Album listings are unwrapped and carry no album of their own.
        page = [{k: v for k, v in track.items() if k != "album"} for track in self.context_tracks[offset : offset + limit]]
        return {"items": page, "total": len(self.context_tracks), "limit": limit, "offset": offset}

    def get_queue(self):
        self._record("GET", Endpoints.PLAYER_QUEUE)
        if not self.queue:
            return {"currently_playing": None, "queue": []}
        return {"currently_playing": self.queue[0], "queue": list(self.queue[1:])}

    def get_context(self, uri):
        self._record("GET", f"context:{uri}")
        return {
            "name": "Today's Top Hits",
            "uri": uri,
            "images": [{"url": "https://i.example/context.jpg", "width": 300}],
        }


def playlist_payload(index: int) -> dict:
    return {
        "id": f"playlist{index}",
        "uri": f"spotify:playlist:playlist{index}",
        "name": f"Playlist {index}",
        "images": [{"url": f"https://i.example/pl{index}.jpg", "width": 300}],
        "tracks": {"total": 10 + index},
        "owner": {"display_name": "Test Listener"},
        "external_urls": {"spotify": f"https://open.spotify.com/playlist/playlist{index}"},
    }


def saved_track_payloads(count: int) -> list[dict]:
    return [track_payload(track_id=f"track{index:04d}", name=f"Song {index}") for index in range(count)]


__all__ = [
    "Call",
    "FakeSpotifyApi",
    "SpotifyApiError",
    "SpotifyNetworkError",
    "SpotifyNoDeviceError",
    "SpotifyRateLimitError",
    "device_payload",
    "episode_payload",
    "playback_payload",
    "playlist_payload",
    "saved_track_payloads",
    "track_payload",
]
