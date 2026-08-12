"""Spotify: Shuffle.

Three visual states, not two: on, off, and "Spotify has not told us yet", which
is what an unknown state honestly is before the first poll answers.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class ShuffleAction(SpotifyActionBase):
    TITLE = "Shuffle"
    ICON = "shuffle"

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_shuffle",
                ui_label="Toggle shuffle",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        self.manager.toggle_shuffle(self.device_id)

    def state_signature(self):
        return (self.blocking_status(), self.manager.get_playback_state().shuffle)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        shuffle = self.manager.get_playback_state().shuffle

        if shuffle is None:
            return self.render_status(ActionStatus.UNKNOWN, detail="SHUFFLE")

        return render_glyph_key(
            self.image_size(),
            "shuffle",
            color=theme.SPOTIFY_GREEN if shuffle else theme.WHITE,
            caption="ON" if shuffle else "OFF",
            active=bool(shuffle),
            dim=not shuffle,
        )
