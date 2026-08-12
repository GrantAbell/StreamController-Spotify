"""Spotify: Setup — connection status, and the way into the plugin settings.

Pressing it opens the plugin's own settings window, which is the only place a
Client ID or an account lives. Nothing account-related is ever stored per action.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

import globals as gl
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..spotify.log import log
from ..spotify.scopes import missing_scopes
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class SetupAction(SpotifyActionBase):
    TITLE = "Spotify"
    ICON = "setup"
    USES_DEVICE_TARGET = False
    TOPICS = frozenset({"auth"})

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_setup_open",
                ui_label="Open Spotify plugin settings",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        # Event callbacks run on a deck thread; GTK windows must be created on
        # the main loop.
        GLib.idle_add(self._open_settings)

    def _open_settings(self) -> bool:
        try:
            from src.windows.Settings.PluginSettingsWindow.PluginSettingsWindow import PluginSettingsWindow

            window = PluginSettingsWindow(self.plugin_base)
            window.present(gl.app.get_active_window())
        except Exception:  # noqa: BLE001 - the deck must not care if the UI is unavailable
            log.exception("Spotify: could not open the plugin settings window")
        return False

    def state_signature(self):
        auth = self.manager.auth
        return (
            auth.is_authenticated,
            auth.is_authenticating,
            auth.last_error,
            bool(self.manager.setting("spotify_client_id", "")),
            self.manager.profile.display_name if self.manager.profile else None,
        )

    def render_image(self):
        auth = self.manager.auth
        size = self.image_size()

        if auth.is_authenticating:
            return self.render_status(ActionStatus.PENDING, detail="SIGNING\nIN")

        if not (self.manager.setting("spotify_client_id", "") or "").strip():
            return self.render_status(ActionStatus.AUTH_REQUIRED, detail="NOT SET\nUP")

        if auth.last_error and not auth.is_authenticated:
            return self.render_status(ActionStatus.API_ERROR, detail="SIGN-IN\nFAILED")

        if not auth.is_authenticated:
            return self.render_status(ActionStatus.AUTH_REQUIRED, detail="CONNECT")

        if missing_scopes(auth.granted_scopes):
            # An older grant is still usable for some things, but some actions
            # will fail until the user authorises again.
            return self.render_status(ActionStatus.AUTH_REQUIRED, detail="REAUTH")

        profile = self.manager.profile
        caption = (profile.display_name if profile and profile.display_name else "CONNECTED")[:12]
        return render_glyph_key(size, "success", color=theme.SPOTIFY_GREEN, caption=caption, active=True)
