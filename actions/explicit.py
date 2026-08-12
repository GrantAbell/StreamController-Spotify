"""Spotify: Explicit Indicator.

Display only, and deliberately free: the explicit flag is already part of the
playback metadata the manager polls, so this action never causes a request of
its own.
"""

from __future__ import annotations

from ..rendering import theme
from ..rendering.key import render_glyph_key, render_text_key
from ..spotify.models import ITEM_TYPE_TRACK
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class ExplicitAction(SpotifyActionBase):
    TITLE = "Explicit"
    ICON = "explicit"
    USES_DEVICE_TARGET = False

    def state_signature(self):
        state = self.manager.get_playback_state()
        track = state.track
        return (
            self.blocking_status(),
            track.uri if track else None,
            track.explicit if track else None,
            track.item_type if track else None,
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        track = self.manager.get_playback_state().track
        size = self.image_size()

        if track is None:
            return self.render_status(ActionStatus.UNKNOWN, detail="?")

        if track.explicit:
            return render_glyph_key(size, "explicit", color=theme.WHITE, caption="EXPLICIT")

        if track.item_type != ITEM_TYPE_TRACK:
            # Podcasts carry the flag too, but "clean" is a music word, so the
            # honest answer here is the item type.
            return render_text_key(size, [track.item_type.upper()], color=theme.MUTED, accent="RATING")

        return render_text_key(size, ["CLEAN"], color=theme.SPOTIFY_GREEN, accent="RATING")
