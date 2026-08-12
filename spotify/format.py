"""Text templating for the clipboard action.

Pure string work, kept out of the action so the placeholder rules can be tested
without GTK. An unknown placeholder is left alone rather than raising, because
the template comes from a user-editable text field.
"""

from __future__ import annotations

import re

from .models import SpotifyTrack

FORMAT_TRACK_ARTIST = "track_artist"
FORMAT_TRACK_ARTIST_URL = "track_artist_url"
FORMAT_URL = "url"
FORMAT_CUSTOM = "custom"

PRESETS = {
    FORMAT_TRACK_ARTIST: "{track} — {artist}",
    FORMAT_TRACK_ARTIST_URL: "{track} — {artist}\n{url}",
    FORMAT_URL: "{url}",
}

PLACEHOLDERS = ("track", "artist", "artists", "album", "url", "uri")

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def values_for(track: SpotifyTrack | None) -> dict[str, str]:
    if track is None:
        return {name: "" for name in PLACEHOLDERS}

    return {
        "track": track.name or "",
        # `artist` is the primary artist, `artists` is all of them — the
        # distinction matters for collaborations.
        "artist": (track.artists[0] if track.artists else ""),
        "artists": track.artist_text,
        "album": track.album_name or "",
        "url": track.external_url or "",
        "uri": track.uri or "",
    }


def render_template(template: str, track: SpotifyTrack | None) -> str:
    values = values_for(track)

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(substitute, template or "")


def format_song(format_id: str, track: SpotifyTrack | None, custom_template: str = "") -> str:
    template = custom_template if format_id == FORMAT_CUSTOM else PRESETS.get(format_id, PRESETS[FORMAT_TRACK_ARTIST])
    return render_template(template, track).strip()
