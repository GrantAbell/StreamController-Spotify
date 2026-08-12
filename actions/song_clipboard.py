"""Spotify: Song Clipboard — copy the current track in a chosen format."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..spotify.format import (
    FORMAT_CUSTOM,
    FORMAT_TRACK_ARTIST,
    FORMAT_TRACK_ARTIST_URL,
    FORMAT_URL,
    PLACEHOLDERS,
)
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase
from .clipboard import copy_current_song


class SongClipboardAction(SpotifyActionBase):
    TITLE = "Copy"
    ICON = "clipboard"
    USES_DEVICE_TARGET = False

    EXTRA_DEFAULTS = {
        "format": FORMAT_TRACK_ARTIST,
        "custom_template": "{track} — {artist}\n{url}",
    }

    def build_action_ui(self) -> None:
        self._format_row = ComboRow(
            action_core=self,
            var_name="format",
            default_value=FORMAT_TRACK_ARTIST,
            items=[
                SimpleComboRowItem(FORMAT_TRACK_ARTIST, "Track — Artist"),
                SimpleComboRowItem(FORMAT_TRACK_ARTIST_URL, "Track — Artist, then the link"),
                SimpleComboRowItem(FORMAT_URL, "Spotify link only"),
                SimpleComboRowItem(FORMAT_CUSTOM, "Custom template"),
            ],
            title="Format",
            subtitle="What gets copied",
        )
        self._template_row = EntryRow(
            action_core=self,
            var_name="custom_template",
            default_value="{track} — {artist}\n{url}",
            title="Custom template",
        )

    def get_extra_config_rows(self) -> list:
        group = Adw.PreferencesGroup(title="Template placeholders")
        group.add(
            Adw.ActionRow(
                title=", ".join(f"{{{name}}}" for name in PLACEHOLDERS),
                subtitle="Anything else is left as typed.",
            )
        )
        return [group]

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_copy_song",
                ui_label="Copy song info",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        copy_current_song(self)

    def state_signature(self):
        track = self.manager.get_playback_state().track
        return (self.blocking_status(), track.uri if track else None)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        if self.manager.get_playback_state().track is None:
            return self.render_status(ActionStatus.UNAVAILABLE, detail="NOTHING\nPLAYING")

        return render_glyph_key(self.image_size(), "clipboard", color=theme.WHITE, caption="COPY")
