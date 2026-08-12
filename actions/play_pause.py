"""Spotify: Play / Pause.

The glyph follows Spotify's actual state rather than alternating on each press,
so a track paused from the phone still shows as paused here.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from .base import SpotifyActionBase


class PlayPauseAction(SpotifyActionBase):
    TITLE = "Playback"
    ICON = "play"

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_play_pause",
                ui_label="Play / Pause",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        self.manager.toggle_playback(self.device_id)

    def state_signature(self):
        state = self.manager.get_playback_state()
        return (self.blocking_status(), state.is_playing, state.has_playback)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        playing = state.is_playing

        return render_glyph_key(
            self.image_size(),
            "pause" if playing else "play",
            color=theme.SPOTIFY_GREEN if playing else theme.WHITE,
        )
