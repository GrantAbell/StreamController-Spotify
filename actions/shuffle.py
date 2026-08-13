"""Spotify: Shuffle.

Four visual states, not two: off, on, smart, and "Spotify has not told us yet",
which is what an unknown state honestly is before the first poll answers.

Smart shuffle is shown but cannot be switched on: Spotify's Web API takes a
boolean for shuffle and offers no way to ask for the smart kind. Pressing the
key therefore turns shuffle off, exactly as it does when plain shuffle is on.
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
        state = self.manager.get_playback_state()
        return (self.blocking_status(), state.shuffle, state.is_smart_shuffle)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        shuffle = state.shuffle
        smart = state.is_smart_shuffle

        if shuffle is None and not smart:
            return self.render_status(ActionStatus.UNKNOWN, detail="SHUFFLE")

        on = smart or bool(shuffle)

        return render_glyph_key(
            self.image_size(),
            "smart_shuffle" if smart else "shuffle",
            color=theme.SPOTIFY_GREEN if on else theme.WHITE,
            caption="SMART" if smart else ("ON" if on else "OFF"),
            active=on,
            dim=not on,
        )
