"""Typed models, and the parsers that turn Spotify JSON into them.

Parsing is defensive on purpose. The playback item can be a track, a podcast
episode, a local file, an ad, `null`, or a type that did not exist when this was
written, and none of those may raise — a parser that throws would take out the
poll loop and freeze every action on the deck.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

ITEM_TYPE_TRACK = "track"
ITEM_TYPE_EPISODE = "episode"

REPEAT_OFF = "off"
REPEAT_TRACK = "track"
REPEAT_CONTEXT = "context"
REPEAT_MODES = (REPEAT_OFF, REPEAT_CONTEXT, REPEAT_TRACK)


@dataclass(frozen=True)
class SpotifyTrack:
    id: str | None
    uri: str
    name: str
    artists: list[str]

    album_name: str | None = None
    album_type: str | None = None

    duration_ms: int = 0
    explicit: bool = False

    artwork_url: str | None = None
    external_url: str | None = None

    item_type: str = ITEM_TYPE_TRACK
    is_local: bool = False

    @property
    def artist_text(self) -> str:
        return ", ".join(self.artists) if self.artists else ""

    @property
    def supports_library(self) -> bool:
        """Whether Like and Add to Playlist can work on this item.

        Local files have no Spotify ID and cannot be saved or added; items
        without a URI cannot be referenced at all.
        """
        return bool(self.uri) and not self.is_local and self.id is not None


@dataclass(frozen=True)
class SpotifyDevice:
    id: str | None
    name: str
    device_type: str

    is_active: bool = False
    is_restricted: bool = False

    volume_percent: int | None = None
    supports_volume: bool = True


@dataclass(frozen=True)
class SpotifyPlaylist:
    id: str
    uri: str
    name: str
    artwork_url: str | None = None
    external_url: str | None = None
    track_count: int | None = None
    owner_name: str | None = None


@dataclass(frozen=True)
class UserProfile:
    account_id: str
    display_name: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    product: str | None = None

    @property
    def is_premium(self) -> bool | None:
        """None when the grant did not include the product field."""
        if self.product is None:
            return None
        return self.product == "premium"


@dataclass(frozen=True)
class PlaybackState:
    item_type: str | None = None

    track: SpotifyTrack | None = None

    is_playing: bool = False

    progress_ms: int | None = None
    duration_ms: int | None = None

    volume_percent: int | None = None

    shuffle: bool | None = None
    repeat_mode: str | None = None

    context_uri: str | None = None
    context_type: str | None = None

    device: SpotifyDevice | None = None

    timestamp_ms: int | None = None

    disallowed_actions: frozenset[str] = frozenset()

    last_updated_monotonic: float = field(default_factory=time.monotonic)

    #: False when Spotify answered 204 (nothing playing anywhere) or the poll
    #: has not produced an answer yet. Distinct from "paused".
    has_playback: bool = False

    def allows(self, action: str) -> bool:
        """Spotify's `actions.disallows` for the current context."""
        return action not in self.disallowed_actions


EMPTY_PLAYBACK = PlaybackState()


# -- parsing ---------------------------------------------------------------


def _as_int(value, fallback: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def pick_image_url(images, target_px: int = 300) -> str | None:
    """The image closest to `target_px` wide, ignoring malformed entries.

    Spotify usually returns 640/300/64 for albums but does not promise an
    ordering or that `width` is present, so the smallest useful one is chosen by
    comparison rather than by index.
    """
    if not images:
        return None

    best_url = None
    best_score = None
    for image in images:
        if not isinstance(image, dict):
            continue
        url = image.get("url")
        if not url:
            continue
        width = _as_int(image.get("width")) or _as_int(image.get("height")) or 0
        score = abs(width - target_px) if width else target_px * 4
        if best_score is None or score < best_score:
            best_score = score
            best_url = url

    return best_url


def parse_track(data: dict | None) -> SpotifyTrack | None:
    """Parse a playback item of any type into the common track shape."""
    if not isinstance(data, dict):
        return None

    item_type = data.get("type") or ITEM_TYPE_TRACK
    uri = data.get("uri") or ""
    name = data.get("name") or "Unknown"
    external_url = (data.get("external_urls") or {}).get("spotify")
    duration_ms = _as_int(data.get("duration_ms"), 0) or 0
    explicit = bool(data.get("explicit"))
    is_local = bool(data.get("is_local")) or uri.startswith("spotify:local:")

    if item_type == ITEM_TYPE_EPISODE:
        show = data.get("show") or {}
        artwork = pick_image_url(data.get("images") or show.get("images"))
        publisher = show.get("publisher") or show.get("name")
        return SpotifyTrack(
            id=data.get("id"),
            uri=uri,
            name=name,
            artists=[publisher] if publisher else [],
            album_name=show.get("name"),
            album_type="show",
            duration_ms=duration_ms,
            explicit=explicit,
            artwork_url=artwork,
            external_url=external_url,
            item_type=ITEM_TYPE_EPISODE,
            is_local=is_local,
        )

    album = data.get("album") if isinstance(data.get("album"), dict) else {}
    artists = [
        artist.get("name")
        for artist in (data.get("artists") or [])
        if isinstance(artist, dict) and artist.get("name")
    ]

    return SpotifyTrack(
        id=data.get("id"),
        uri=uri,
        name=name,
        artists=artists,
        album_name=album.get("name"),
        album_type=album.get("album_type"),
        duration_ms=duration_ms,
        explicit=explicit,
        artwork_url=pick_image_url(album.get("images")),
        external_url=external_url,
        # An unfamiliar future type still renders as a titled item rather than
        # being dropped; only music-specific actions check the type.
        item_type=item_type,
        is_local=is_local,
    )


def parse_device(data: dict | None) -> SpotifyDevice | None:
    if not isinstance(data, dict):
        return None

    return SpotifyDevice(
        id=data.get("id"),
        name=data.get("name") or "Spotify device",
        device_type=data.get("type") or "Unknown",
        is_active=bool(data.get("is_active")),
        is_restricted=bool(data.get("is_restricted")),
        volume_percent=_as_int(data.get("volume_percent")),
        # Absent means the device does support volume; only an explicit false
        # means it does not.
        supports_volume=data.get("supports_volume") is not False,
    )


def parse_devices(payload: dict | None) -> list[SpotifyDevice]:
    devices = (payload or {}).get("devices") or []
    parsed = [parse_device(device) for device in devices]
    return [device for device in parsed if device is not None]


def parse_playback(payload: dict | None) -> PlaybackState:
    """Parse `GET /me/player`. An empty payload means nothing is playing."""
    now = time.monotonic()

    if not isinstance(payload, dict) or not payload:
        return PlaybackState(last_updated_monotonic=now, has_playback=False)

    item = payload.get("item")
    track = parse_track(item)
    item_type = payload.get("currently_playing_type") or (track.item_type if track else None)

    context = payload.get("context") or {}
    device = parse_device(payload.get("device"))

    disallows = (payload.get("actions") or {}).get("disallows") or {}
    disallowed = frozenset(name for name, value in disallows.items() if value)

    repeat = payload.get("repeat_state")
    if repeat not in REPEAT_MODES:
        repeat = None

    shuffle = payload.get("shuffle_state")
    shuffle = bool(shuffle) if isinstance(shuffle, bool) else None

    return PlaybackState(
        item_type=item_type,
        track=track,
        is_playing=bool(payload.get("is_playing")),
        progress_ms=_as_int(payload.get("progress_ms")),
        duration_ms=track.duration_ms if track else None,
        volume_percent=device.volume_percent if device else None,
        shuffle=shuffle,
        repeat_mode=repeat,
        context_uri=context.get("uri"),
        context_type=context.get("type"),
        device=device,
        timestamp_ms=_as_int(payload.get("timestamp")),
        disallowed_actions=disallowed,
        last_updated_monotonic=now,
        has_playback=True,
    )


def parse_playlist(data: dict | None) -> SpotifyPlaylist | None:
    if not isinstance(data, dict):
        return None
    playlist_id = data.get("id")
    if not playlist_id:
        return None

    tracks = data.get("tracks") if isinstance(data.get("tracks"), dict) else {}
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}

    return SpotifyPlaylist(
        id=playlist_id,
        uri=data.get("uri") or f"spotify:playlist:{playlist_id}",
        name=data.get("name") or "Untitled playlist",
        artwork_url=pick_image_url(data.get("images")),
        external_url=(data.get("external_urls") or {}).get("spotify"),
        track_count=_as_int(tracks.get("total")),
        owner_name=owner.get("display_name"),
    )


def parse_playlists(payload: dict | None) -> list[SpotifyPlaylist]:
    items = (payload or {}).get("items") or []
    parsed = [parse_playlist(item) for item in items]
    return [playlist for playlist in parsed if playlist is not None]


def parse_saved_tracks(payload: dict | None) -> list[SpotifyTrack]:
    """Parse `GET /me/tracks`, whose items wrap the track in `added_at`."""
    items = (payload or {}).get("items") or []
    tracks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        track = parse_track(item.get("track"))
        if track is not None:
            tracks.append(track)
    return tracks


def parse_profile(data: dict | None) -> UserProfile | None:
    if not isinstance(data, dict):
        return None
    account_id = data.get("id")
    if not account_id:
        return None

    return UserProfile(
        account_id=account_id,
        display_name=data.get("display_name") or account_id,
        image_url=pick_image_url(data.get("images"), target_px=160),
        external_url=(data.get("external_urls") or {}).get("spotify"),
        product=data.get("product"),
    )
