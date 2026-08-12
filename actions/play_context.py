"""Spotify: Play Context — start a saved album, artist, playlist or track.

The link is validated as it is typed, so a wrong paste is obvious in the
settings window rather than becoming a request Spotify refuses later.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_artwork_key, render_glyph_key
from ..spotify.state import ActionStatus
from ..spotify.uri import parse_resource
from .base import SpotifyActionBase

TYPE_LABELS = {
    "album": "ALBUM",
    "artist": "ARTIST",
    "playlist": "PLAYLIST",
    "track": "TRACK",
    "episode": "EPISODE",
}


class PlayContextAction(SpotifyActionBase):
    TITLE = "Play"
    ICON = "play_context"

    EXTRA_DEFAULTS = {"spotify_resource": "", "show_artwork": True}

    def __init__(self, *args, **kwargs):
        self._validation_row: Adw.ActionRow | None = None
        super().__init__(*args, **kwargs)

    def build_action_ui(self) -> None:
        self._resource_row = EntryRow(
            action_core=self,
            var_name="spotify_resource",
            default_value="",
            title="Spotify link or URI",
            on_change=self._on_resource_changed,
        )
        self._artwork_row = SwitchRow(
            action_core=self,
            var_name="show_artwork",
            default_value=True,
            title="Cover art",
            subtitle="Show the playlist or album cover on the key.",
            on_change=self._on_resource_changed,
        )

    def get_extra_config_rows(self) -> list:
        group = Adw.PreferencesGroup(title="What will play")
        self._validation_row = Adw.ActionRow(title="", subtitle="Paste a Spotify link, or a spotify: URI.")
        group.add(self._validation_row)
        self._update_validation_row()
        return [group]

    def _on_resource_changed(self, _widget, _new_value, _old_value) -> None:
        self._update_validation_row()
        self._request_details()
        self._last_signature = None
        self.render()

    def on_action_ready(self) -> None:
        self._request_details()

    def _request_details(self) -> None:
        """Ask the manager to resolve the configured link's name and cover."""
        resource = self.resource()
        if resource is not None:
            self.manager.ensure_context_details(resource.uri)

    def name(self) -> str | None:
        """What the link points at, once Spotify has been asked."""
        resource = self.resource()
        return self.manager.get_context_name(resource.uri) if resource else None

    def artwork(self):
        """The cover for the configured link, once it has been fetched."""
        resource = self.resource()
        if resource is None or not self.setting("show_artwork", True):
            return None
        return self.manager.artwork.get(self.manager.get_context_artwork_url(resource.uri))

    def _update_validation_row(self) -> None:
        if self._validation_row is None:
            return
        resource = self.resource()
        if resource is None:
            text = self.setting("spotify_resource", "")
            self._validation_row.set_title("Nothing set" if not text else "Not a Spotify link")
        else:
            self._validation_row.set_title(f"{TYPE_LABELS.get(resource.resource_type, resource.resource_type)} · {resource.resource_id}")

    def resource(self):
        return parse_resource(self.setting("spotify_resource", ""))

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_play_context",
                ui_label="Play this",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        resource = self.resource()
        if resource is None:
            self.report_failure()
            return
        # play_context routes a track or episode through `uris` rather than
        # `context_uri`, which is what Spotify requires for a single item.
        self.manager.play_context(resource.uri, self.device_id)

    def state_signature(self):
        resource = self.resource()
        return (
            self.blocking_status(),
            resource.uri if resource else None,
            # The name and the cover both arrive after the first draw, so they
            # are part of what makes the drawn result different.
            self.name(),
            self.artwork() is not None,
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        resource = self.resource()
        if resource is None:
            return self.render_status(ActionStatus.UNKNOWN, detail="NO LINK")

        # Nothing resolved yet, or nothing to draw yet: ask, and stand in with
        # what is already known — the kind of thing the link points at.
        self._request_details()
        name = self.name()
        type_label = TYPE_LABELS.get(resource.resource_type, resource.resource_type.upper())

        artwork = self.artwork()
        if artwork is not None:
            return render_artwork_key(
                self.image_size(),
                artwork,
                caption=name or type_label,
                caption_color=theme.WHITE if name else theme.SPOTIFY_GREEN,
            )

        return render_glyph_key(
            self.image_size(),
            "play_context",
            color=theme.SPOTIFY_GREEN,
            caption=name or type_label,
        )
