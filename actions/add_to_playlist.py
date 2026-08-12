"""Spotify: Add to Playlist — put the current song into a chosen playlist.

Matching the reference behaviour, this does not scan the playlist for duplicates
first: a press means "add this", every time. Scanning a large playlist would
cost several requests per press for a check the user did not ask for.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_text_key
from ..spotify.state import ActionStatus, is_music_track
from .base import SpotifyActionBase


class AddToPlaylistAction(SpotifyActionBase):
    TITLE = "Add to"
    ICON = "add_to_playlist"
    USES_DEVICE_TARGET = False
    TOPICS = frozenset({"playback", "auth", "playlists"})

    EXTRA_DEFAULTS = {
        "playlist_id": "",
        "playlist_name": "",
    }

    def __init__(self, *args, **kwargs):
        self._playlist_group: Adw.PreferencesGroup | None = None
        self._playlist_rows: list = []
        super().__init__(*args, **kwargs)

    def on_action_ready(self) -> None:
        # Warms the cache so the settings list is populated when it is opened.
        self.manager.get_playlists()

    # -- settings UI ------------------------------------------------------

    def get_extra_config_rows(self) -> list:
        self._playlist_group = Adw.PreferencesGroup(
            title="Playlist",
            description="Each press adds the current song again; duplicates are not checked for.",
        )
        self._refresh_playlist_rows()
        return [self._playlist_group]

    def _refresh_playlist_rows(self) -> None:
        group = self._playlist_group
        if group is None:
            return

        for row in self._playlist_rows:
            group.remove(row)
        self._playlist_rows = []

        settings = self.settings()
        selected_id = settings.get("playlist_id") or ""
        playlists = self.manager.get_playlists()

        if playlists is None:
            row = Adw.ActionRow(title="Loading your playlists…")
            group.add(row)
            self._playlist_rows.append(row)
        elif not playlists:
            row = Adw.ActionRow(title="No playlists found", subtitle="Create one in Spotify, then refresh.")
            group.add(row)
            self._playlist_rows.append(row)
        else:
            for playlist in playlists:
                row = Adw.ActionRow(
                    title=playlist.name,
                    subtitle=f"{playlist.track_count} songs" if playlist.track_count is not None else "",
                )
                button = Gtk.CheckButton(active=playlist.id == selected_id, valign=Gtk.Align.CENTER)
                button.connect("toggled", self._on_playlist_chosen, playlist.id, playlist.name)
                row.add_prefix(button)
                group.add(row)
                self._playlist_rows.append(row)

        if selected_id and playlists and all(playlist.id != selected_id for playlist in playlists):
            row = Adw.ActionRow(
                title=f"{settings.get('playlist_name') or 'Saved playlist'} was not found",
                subtitle=selected_id,
            )
            group.add(row)
            self._playlist_rows.append(row)

        refresh = Adw.ActionRow(title="Refresh playlists")
        button = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        button.connect("clicked", self._on_refresh)
        refresh.add_suffix(button)
        group.add(refresh)
        self._playlist_rows.append(refresh)

    def _on_refresh(self, _button) -> None:
        self.manager.refresh_playlists()
        self._refresh_playlist_rows()

    def _on_playlist_chosen(self, button, playlist_id: str, playlist_name: str) -> None:
        if not button.get_active():
            return
        settings = self.settings()
        settings["playlist_id"] = playlist_id
        settings["playlist_name"] = playlist_name
        self.set_settings(settings)
        self._last_signature = None
        self.render()

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_add_to_playlist",
                ui_label="Add current song",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        playlist_id = self.setting("playlist_id", "")
        state = self.manager.get_playback_state()

        if not playlist_id or state.track is None:
            self.report_failure()
            return

        self.manager.add_current_to_playlist(
            playlist_id,
            on_result=lambda ok: self.flash("ADDED") if ok else self.report_failure(),
        )

    # -- rendering --------------------------------------------------------

    def _playlist_exists(self) -> bool | None:
        playlists = self.manager.get_playlists()
        if playlists is None:
            return None
        playlist_id = self.setting("playlist_id", "")
        return any(playlist.id == playlist_id for playlist in playlists)

    def state_signature(self):
        return (
            self.blocking_status(),
            self.setting("playlist_id"),
            self._playlist_exists(),
            is_music_track(self.manager.get_playback_state()),
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        settings = self.settings()
        if not settings.get("playlist_id"):
            return self.render_status(ActionStatus.UNKNOWN, detail="PICK\nPLAYLIST")

        if self._playlist_exists() is False:
            return self.render_status(ActionStatus.UNAVAILABLE, detail="PLAYLIST\nMISSING")

        name = settings.get("playlist_name") or "Playlist"
        return render_text_key(
            self.image_size(),
            [name],
            color=theme.WHITE,
            accent="ADD TO",
            accent_color=theme.SPOTIFY_GREEN,
        )
