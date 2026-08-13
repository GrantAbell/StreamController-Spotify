"""The OAuth scopes this plugin asks for, and why each one is needed.

Only what the shipped actions actually use. `user-read-email` is deliberately
absent: nothing here needs the account's email address, and asking for it would
make the consent screen scarier than the plugin's behaviour warrants.
"""

from __future__ import annotations

#: scope -> the feature that would break without it
SCOPE_REASONS: dict[str, str] = {
    "user-read-playback-state": "Read what is playing, and which devices exist",
    "user-modify-playback-state": "Play, pause, skip, seek, shuffle, repeat, volume, queue, transfer",
    "user-library-read": "Show whether the current song is in your Liked Songs",
    "user-library-modify": "Like and unlike the current song",
    "playlist-read-private": "List your playlists, including private ones",
    "playlist-modify-public": "Add the current song to a public playlist",
    "playlist-modify-private": "Add the current song to a private playlist",
    "user-read-private": "Show your display name in the User Information action",
}

REQUIRED_SCOPES: tuple[str, ...] = tuple(SCOPE_REASONS)

SCOPE_STRING = " ".join(REQUIRED_SCOPES)


def missing_scopes(granted: list[str] | tuple[str, ...] | None) -> list[str]:
    """Scopes the plugin needs that this grant does not include.

    Spotify returns the granted scopes with the token, and a user who authorised
    an older version of the plugin keeps that narrower grant until they
    re-authenticate — so this is checked rather than assumed.
    """
    have = set(granted or ())
    return [scope for scope in REQUIRED_SCOPES if scope not in have]
