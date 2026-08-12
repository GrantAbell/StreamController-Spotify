"""Spotify: Like / Unlike.

Liked state is looked up when the track changes, not on a timer, and the key is
explicit about not knowing rather than guessing. Items that cannot be saved at
all — podcast episodes, local files — say so instead of failing on press.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..spotify.state import ActionStatus, LikeState, is_music_track
from .base import SpotifyActionBase


class LikeAction(SpotifyActionBase):
    TITLE = "Library"
    ICON = "library_add"
    USES_DEVICE_TARGET = False

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_like",
                ui_label="Like / Unlike",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def on_action_ready(self) -> None:
        # The track may have changed while this page was not showing.
        self.manager.refresh_like_state()

    def _on_press(self, _data=None) -> None:
        if not is_music_track(self.manager.get_playback_state()):
            self.report_failure()
            return
        self.manager.toggle_like(on_result=lambda liked: self.flash("LIKED" if liked else "REMOVED"))

    def state_signature(self):
        state = self.manager.get_playback_state()
        return (
            self.blocking_status(),
            self.manager.get_like_state(),
            is_music_track(state),
            state.track.uri if state.track else None,
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        if state.track is not None and not is_music_track(state):
            return self.render_status(ActionStatus.UNAVAILABLE, detail="NOT\nAVAILABLE")

        like_state = self.manager.get_like_state()
        size = self.image_size()

        if like_state is LikeState.LIKED:
            return render_glyph_key(size, "library_saved", color=theme.SPOTIFY_GREEN, caption="LIKED", active=True)
        if like_state is LikeState.NOT_LIKED:
            return render_glyph_key(size, "library_add", color=theme.WHITE, caption="LIKE")
        if like_state is LikeState.BUSY:
            return self.render_status(ActionStatus.BUSY, detail="…")
        return self.render_status(ActionStatus.UNKNOWN, detail="LIKE?")
