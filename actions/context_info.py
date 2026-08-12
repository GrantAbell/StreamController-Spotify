"""Spotify: Context Information — what the current song is playing from.

The context name is resolved once per URI and cached by the manager, so a key
sitting on a page all afternoon costs one request per distinct playlist, not one
per second.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_text_key
from ..spotify.state import ActionStatus
from ..spotify.uri import uri_type
from .base import SpotifyActionBase

CONTEXT_LABELS = {
    "playlist": "PLAYLIST",
    "album": "ALBUM",
    "artist": "ARTIST",
    "show": "PODCAST",
    "collection": "LIKED SONGS",
}


class ContextInfoAction(SpotifyActionBase):
    TITLE = "Context"
    ICON = "context"
    USES_DEVICE_TARGET = False

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_open_context",
                ui_label="Open the context in Spotify",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        context_uri = self.manager.get_playback_state().context_uri
        if not context_uri or not self.manager.open_in_spotify(context_uri):
            self.report_failure()

    def state_signature(self):
        state = self.manager.get_playback_state()
        return (
            self.blocking_status(),
            state.context_uri,
            state.context_type,
            self.manager.get_context_name(state.context_uri),
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        if not state.context_uri:
            return self.render_status(ActionStatus.UNKNOWN, detail="NO\nCONTEXT")

        context_type = state.context_type or uri_type(state.context_uri) or "unknown"
        label = CONTEXT_LABELS.get(context_type, context_type.upper())
        name = self.manager.get_context_name(state.context_uri)

        return render_text_key(
            self.image_size(),
            _wrap(name) if name else ["…"],
            color=theme.WHITE,
            accent=label,
            accent_color=theme.SPOTIFY_GREEN,
        )


def _wrap(name: str, max_lines: int = 2) -> list[str]:
    """Split a context name across at most two lines, on word boundaries."""
    words = name.split()
    if len(words) <= 1:
        return [name]

    midpoint = len(name) / 2
    best_index, best_distance = 1, None
    length = 0
    for index, word in enumerate(words[:-1]):
        length += len(word) + 1
        distance = abs(length - midpoint)
        if best_distance is None or distance < best_distance:
            best_distance, best_index = distance, index + 1

    lines = [" ".join(words[:best_index]), " ".join(words[best_index:])]
    return lines[:max_lines]
