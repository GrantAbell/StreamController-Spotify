"""Parsing of the three shapes a user can paste: URI, share URL, or bare ID.

Everything is normalised to a Spotify URI internally, so no other module has to
care which form the user had on their clipboard. Malformed input is rejected
here rather than turning into a request Spotify will refuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

#: Types the plugin knows how to act on.
PLAYABLE_CONTEXT_TYPES = ("album", "artist", "playlist")
PLAYABLE_ITEM_TYPES = ("track", "episode")
KNOWN_TYPES = ("track", "album", "artist", "playlist", "episode", "show", "user", "collection")

# Spotify IDs are base62 and 22 characters today, but the length is not part of
# any published guarantee, so only the alphabet is enforced.
_ID_RE = re.compile(r"^[A-Za-z0-9]+$")

_SPOTIFY_HOSTS = ("open.spotify.com", "play.spotify.com")


@dataclass(frozen=True)
class SpotifyResource:
    resource_type: str
    resource_id: str
    uri: str

    @property
    def is_context(self) -> bool:
        """True if this can be handed to the player as a `context_uri`."""
        return self.resource_type in PLAYABLE_CONTEXT_TYPES

    @property
    def is_item(self) -> bool:
        """True if this must be played through the `uris` list instead."""
        return self.resource_type in PLAYABLE_ITEM_TYPES

    @property
    def external_url(self) -> str:
        return f"https://open.spotify.com/{self.resource_type}/{self.resource_id}"


def parse_resource(value: str) -> SpotifyResource | None:
    """Normalise a URI, share URL or bare ID. Returns None if unusable.

    Query parameters (`?si=…` tracking tags on shared links) are dropped, and so
    are locale path prefixes like `/intl-de/`, both of which appear routinely in
    links copied out of the Spotify apps.
    """
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    if text.startswith("spotify:"):
        return _parse_uri(text)

    if "://" in text or text.startswith("open.spotify.com") or text.startswith("play.spotify.com"):
        return _parse_url(text)

    # A bare ID is ambiguous on its own; callers that accept one say what it is.
    return None


def parse_id(resource_type: str, value: str) -> SpotifyResource | None:
    """Parse a value that is allowed to be a bare ID of a known type."""
    if not value:
        return None
    text = value.strip()
    if _ID_RE.match(text):
        return make_resource(resource_type, text)
    resource = parse_resource(text)
    if resource and resource.resource_type == resource_type:
        return resource
    return None


def make_resource(resource_type: str, resource_id: str) -> SpotifyResource | None:
    if resource_type not in KNOWN_TYPES or not _ID_RE.match(resource_id or ""):
        return None
    return SpotifyResource(resource_type, resource_id, f"spotify:{resource_type}:{resource_id}")


def _parse_uri(text: str) -> SpotifyResource | None:
    parts = text.split(":")

    # spotify:user:<name>:playlist:<id> — the legacy playlist form still found
    # in older links and in some third-party exports.
    if len(parts) == 5 and parts[1] == "user" and parts[3] == "playlist":
        return make_resource("playlist", parts[4])

    if len(parts) != 3:
        return None

    _, resource_type, resource_id = parts
    return make_resource(resource_type, resource_id)


def _parse_url(text: str) -> SpotifyResource | None:
    if "://" not in text:
        text = "https://" + text

    parsed = urlparse(text)
    if parsed.hostname not in _SPOTIFY_HOSTS:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]

    # Locale prefixes ("intl-de") sit in front of the real type segment.
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]

    # Legacy /user/<name>/playlist/<id> URLs.
    if len(segments) == 4 and segments[0] == "user" and segments[2] == "playlist":
        return make_resource("playlist", segments[3])

    if len(segments) < 2:
        return None

    return make_resource(segments[0], segments[1])


def uri_type(uri: str) -> str | None:
    """The type segment of a URI, without validating the ID."""
    if not uri or not uri.startswith("spotify:"):
        return None
    parts = uri.split(":")
    if len(parts) < 3:
        return None
    return parts[1]


def external_url_for_uri(uri: str) -> str | None:
    resource = parse_resource(uri) if uri else None
    return resource.external_url if resource else None


def open_targets(url_or_uri: str | None, *, prefer_app: bool = True) -> list[str]:
    """What to hand the desktop, in order, to open something in Spotify.

    Which form is handed over is what decides where the link lands: the desktop
    app registers the `spotify:` URI scheme, while `https://open.spotify.com/…`
    belongs to whatever owns http links — the browser. The web player is kept
    as the second try, since it works even with no app installed.
    """
    if not url_or_uri:
        return []

    resource = parse_resource(url_or_uri)
    if resource is not None:
        app_uri, web_url = resource.uri, resource.external_url
    else:
        app_uri, web_url = _loose_forms(url_or_uri)

    targets = [web_url] if web_url else []
    if app_uri and prefer_app:
        targets.insert(0, app_uri)

    # Nothing recognisable: hand over what the caller gave us rather than
    # deciding it cannot be opened.
    return targets or [url_or_uri]


def _loose_forms(value: str) -> tuple[str | None, str | None]:
    """Both forms of a Spotify link whose ID the strict parser rejects.

    Only user profiles hit this in practice: account IDs older than the base62
    scheme do exist, and one should still open in the app rather than nowhere.
    Nothing here is used to build a request — only to open a link — so the ID
    is passed along as found instead of being validated.
    """
    text = value.strip()

    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) != 3 or parts[1] not in KNOWN_TYPES or not parts[2]:
            return (None, None)
        return (text, f"https://open.spotify.com/{parts[1]}/{parts[2]}")

    parsed = urlparse(text if "://" in text else "https://" + text)
    if parsed.hostname not in _SPOTIFY_HOSTS:
        return (None, None)

    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]
    if len(segments) < 2 or segments[0] not in KNOWN_TYPES:
        return (None, None)

    return (
        f"spotify:{segments[0]}:{segments[1]}",
        f"https://open.spotify.com/{segments[0]}/{segments[1]}",
    )
