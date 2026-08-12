"""The plugin-wide settings page: the account, and everything global.

The Spotify account lives here and only here. Actions never hold credentials,
because action settings are stored in the page file and travel with an exported
or duplicated page.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ..spotify.auth import DEFAULT_CALLBACK_PORT
from ..spotify.log import log
from ..spotify.scopes import SCOPE_REASONS, missing_scopes

PREMIUM_NOTE = (
    "Spotify Premium is required. Spotify's playback endpoints refuse commands from free accounts, "
    "and Development Mode apps must be owned by a Premium account."
)


class SpotifySettingsArea:
    """Builds the settings UI and keeps its status rows in step with the manager."""

    def __init__(self, plugin):
        self.plugin = plugin
        self._status_row: Adw.ActionRow | None = None
        self._account_row: Adw.ActionRow | None = None
        self._connect_button: Gtk.Button | None = None
        self._disconnect_button: Gtk.Button | None = None
        self._redirect_row: Adw.ActionRow | None = None

    @property
    def manager(self):
        return self.plugin.spotify

    # -- construction -----------------------------------------------------

    def build(self) -> Adw.PreferencesGroup:
        outer = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        outer.add(box)

        box.append(self._build_account_group())
        box.append(self._build_behaviour_group())
        box.append(self._build_about_group())

        self.refresh()
        return outer

    def _build_account_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Spotify account",
            description="You supply your own Spotify app Client ID. No client secret is ever needed or stored.",
        )

        premium = Adw.ActionRow(title="Spotify Premium required", subtitle=PREMIUM_NOTE)
        premium.set_subtitle_lines(3)
        group.add(premium)

        client_id_row = Adw.EntryRow(title="Client ID")
        client_id_row.set_text(self.plugin.plugin_setting("spotify_client_id", ""))
        client_id_row.connect("changed", self._on_client_id_changed)
        group.add(client_id_row)

        self._redirect_row = Adw.ActionRow(
            title="Redirect URI",
            subtitle="Add this exactly, in your Spotify app's settings.",
        )
        copy_button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
        copy_button.connect("clicked", self._on_copy_redirect)
        self._redirect_row.add_suffix(copy_button)
        group.add(self._redirect_row)

        port_row = Adw.SpinRow.new_with_range(1024, 65535, 1)
        port_row.set_title("Callback port")
        port_row.set_subtitle("Only change this if something else already uses the port.")
        port_row.set_value(self.plugin.plugin_setting("callback_port", DEFAULT_CALLBACK_PORT))
        port_row.connect("changed", self._on_port_changed)
        group.add(port_row)

        self._status_row = Adw.ActionRow(title="Status", subtitle="")
        group.add(self._status_row)

        self._account_row = Adw.ActionRow(title="Account", subtitle="")
        group.add(self._account_row)

        buttons = Adw.ActionRow(title="Connection")
        self._connect_button = Gtk.Button(label="Authenticate", valign=Gtk.Align.CENTER)
        self._connect_button.add_css_class("suggested-action")
        self._connect_button.connect("clicked", self._on_connect)
        buttons.add_suffix(self._connect_button)

        self._disconnect_button = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
        self._disconnect_button.add_css_class("destructive-action")
        self._disconnect_button.connect("clicked", self._on_disconnect)
        buttons.add_suffix(self._disconnect_button)
        group.add(buttons)

        scopes = Adw.ExpanderRow(title="What this plugin asks Spotify for")
        for scope, reason in SCOPE_REASONS.items():
            scopes.add_row(Adw.ActionRow(title=scope, subtitle=reason))
        group.add(scopes)

        return group

    def _build_behaviour_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Behaviour",
            description="Defaults for new actions, and how often Spotify is polled.",
        )

        poll_row = Adw.SpinRow.new_with_range(500, 10000, 100)
        poll_row.set_title("Playback poll interval")
        poll_row.set_subtitle("Milliseconds. One request covers every action on the deck.")
        poll_row.set_value(self.plugin.plugin_setting("playback_poll_interval_ms", 1000))
        poll_row.connect("changed", lambda row: self.plugin.set_plugin_setting("playback_poll_interval_ms", int(row.get_value())))
        group.add(poll_row)

        device_row = Adw.SpinRow.new_with_range(5000, 120000, 1000)
        device_row.set_title("Device refresh interval")
        device_row.set_subtitle("Milliseconds between device list refreshes.")
        device_row.set_value(self.plugin.plugin_setting("device_refresh_interval_ms", 15000))
        device_row.connect("changed", lambda row: self.plugin.set_plugin_setting("device_refresh_interval_ms", int(row.get_value())))
        group.add(device_row)

        volume_row = Adw.SpinRow.new_with_range(1, 50, 1)
        volume_row.set_title("Default volume step")
        volume_row.set_value(self.plugin.plugin_setting("default_volume_step", 5))
        volume_row.connect("changed", lambda row: self.plugin.set_plugin_setting("default_volume_step", int(row.get_value())))
        group.add(volume_row)

        seek_row = Adw.SpinRow.new_with_range(1, 120, 1)
        seek_row.set_title("Default seek amount")
        seek_row.set_subtitle("Seconds.")
        seek_row.set_value(self.plugin.plugin_setting("default_seek_seconds", 5))
        seek_row.connect("changed", lambda row: self.plugin.set_plugin_setting("default_seek_seconds", int(row.get_value())))
        group.add(seek_row)

        marquee_row = Adw.SwitchRow(title="Scroll long text", subtitle="Applies to every action that shows a title.")
        marquee_row.set_active(bool(self.plugin.plugin_setting("marquee_enabled", True)))
        marquee_row.connect("notify::active", self._on_marquee_toggled)
        group.add(marquee_row)

        speed_row = Adw.SpinRow.new_with_range(8, 120, 1)
        speed_row.set_title("Scroll speed")
        speed_row.set_subtitle("Pixels per second.")
        speed_row.set_value(self.plugin.plugin_setting("marquee_speed_px_per_second", 32))
        speed_row.connect("changed", self._on_marquee_speed_changed)
        group.add(speed_row)

        debug_row = Adw.SwitchRow(
            title="Debug logging",
            subtitle="Extra detail in StreamController's log. Never includes tokens.",
        )
        debug_row.set_active(bool(self.plugin.plugin_setting("debug_logging", False)))
        debug_row.connect("notify::active", self._on_debug_toggled)
        group.add(debug_row)

        return group

    def _build_about_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="About")
        group.add(
            Adw.ActionRow(
                title="Spotify content",
                subtitle="Track, album and playlist information and artwork come from the Spotify Web API and "
                "remain the property of Spotify and its licensors. This plugin is not affiliated with Spotify.",
            )
        )
        group.add(
            Adw.ActionRow(
                title="Creating your Spotify app",
                subtitle="developer.spotify.com → Dashboard → Create app → add the Redirect URI above → "
                "copy the Client ID here.",
            )
        )
        return group

    # -- handlers ---------------------------------------------------------

    def _on_client_id_changed(self, row) -> None:
        self.plugin.set_plugin_setting("spotify_client_id", row.get_text().strip())
        self.refresh()

    def _on_port_changed(self, row) -> None:
        self.plugin.set_plugin_setting("callback_port", int(row.get_value()))
        self.refresh()

    def _on_copy_redirect(self, _button) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(self.manager.auth.redirect_uri)

    def _on_connect(self, _button) -> None:
        self.manager.auth.authenticate()
        self.refresh()

    def _on_disconnect(self, _button) -> None:
        self.manager.auth.disconnect()
        self.refresh()

    def _on_marquee_toggled(self, row, _param) -> None:
        self.plugin.set_plugin_setting("marquee_enabled", row.get_active())
        self.plugin.apply_marquee_settings()

    def _on_marquee_speed_changed(self, row) -> None:
        self.plugin.set_plugin_setting("marquee_speed_px_per_second", int(row.get_value()))
        self.plugin.apply_marquee_settings()

    def _on_debug_toggled(self, row, _param) -> None:
        self.plugin.set_plugin_setting("debug_logging", row.get_active())
        self.plugin.apply_debug_logging()

    # -- status -----------------------------------------------------------

    def refresh(self) -> None:
        """Update the status rows. Safe to call from any thread."""
        GLib.idle_add(self._do_refresh)

    def _do_refresh(self) -> bool:
        try:
            self._update_rows()
        except Exception:  # noqa: BLE001 - the UI may already be gone
            log.debug("Spotify: settings UI refresh skipped")
        return False

    def _update_rows(self) -> None:
        auth = self.manager.auth

        if self._redirect_row is not None:
            self._redirect_row.set_title(auth.redirect_uri)

        if self._status_row is not None:
            self._status_row.set_subtitle(self._status_text())

        if self._account_row is not None:
            profile = self.manager.profile
            if profile is None:
                self._account_row.set_subtitle("Not connected")
            else:
                premium = profile.is_premium
                suffix = "" if premium is None else (" · Premium" if premium else " · not Premium")
                self._account_row.set_subtitle(f"{profile.display_name or profile.account_id}{suffix}")

        if self._connect_button is not None:
            if auth.is_authenticating:
                self._connect_button.set_label("Waiting for browser…")
                self._connect_button.set_sensitive(False)
            else:
                self._connect_button.set_label("Reauthenticate" if auth.is_authenticated else "Authenticate")
                self._connect_button.set_sensitive(bool(auth.client_id))

        if self._disconnect_button is not None:
            self._disconnect_button.set_sensitive(auth.is_authenticated or auth.is_authenticating)

    def _status_text(self) -> str:
        auth = self.manager.auth

        if not auth.client_id:
            return "Enter your Client ID to begin."
        if auth.is_authenticating:
            return f"Waiting for you to authorise in the browser. Listening on {auth.redirect_uri}"
        if auth.last_error:
            return auth.last_error
        if not auth.is_authenticated:
            return "Not connected."

        missing = missing_scopes(auth.granted_scopes)
        if missing:
            return "Connected, but some permissions are missing: " + ", ".join(missing) + ". Reauthenticate to fix."

        if self.manager.is_rate_limited:
            return "Connected. Spotify is currently rate limiting requests."

        return "Connected."
