"""Spotify: Loop Context and Loop Song.

Each key owns one repeat mode and toggles between it and off, so two keys placed
side by side always agree about what Spotify is doing.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..spotify.models import REPEAT_CONTEXT, REPEAT_TRACK
from ..spotify.state import ActionStatus, toggle_repeat_mode
from .base import SpotifyActionBase


class _RepeatActionBase(SpotifyActionBase):
    MODE = REPEAT_CONTEXT

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_repeat",
                ui_label="Toggle repeat mode",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        current = self.manager.get_playback_state().repeat_mode
        self.manager.set_repeat(toggle_repeat_mode(current, self.MODE), self.device_id)

    def state_signature(self):
        return (self.blocking_status(), self.manager.get_playback_state().repeat_mode)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        mode = self.manager.get_playback_state().repeat_mode
        if mode is None:
            return self.render_status(ActionStatus.UNKNOWN, detail=self.TITLE.upper())

        is_on = mode == self.MODE
        return render_glyph_key(
            self.image_size(),
            self.ICON,
            color=theme.SPOTIFY_GREEN if is_on else theme.WHITE,
            caption="ON" if is_on else "OFF",
            active=is_on,
            dim=not is_on,
        )


class LoopContextAction(_RepeatActionBase):
    TITLE = "Loop"
    ICON = "repeat_context"
    MODE = REPEAT_CONTEXT


class LoopSongAction(_RepeatActionBase):
    TITLE = "Loop 1"
    ICON = "repeat_track"
    MODE = REPEAT_TRACK
