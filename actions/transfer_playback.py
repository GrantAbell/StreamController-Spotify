"""Spotify: Transfer Playback — move playback to a chosen device.

The device is chosen explicitly and is never silently substituted: if the saved
device is not currently available, the key says so rather than sending your
music somewhere you did not ask for.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_text_key
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class TransferPlaybackAction(SpotifyActionBase):
    TITLE = "Transfer"
    ICON = "device_transfer"
    USES_DEVICE_TARGET = False
    TOPICS = frozenset({"playback", "auth", "devices"})

    EXTRA_DEFAULTS = {
        "device_id": "",
        "device_name": "",
        "start_playing": False,
    }

    def __init__(self, *args, **kwargs):
        self._target_group: Adw.PreferencesGroup | None = None
        self._target_rows: list = []
        super().__init__(*args, **kwargs)

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._start_row = SwitchRow(
            action_core=self,
            var_name="start_playing",
            default_value=False,
            title="Start playing",
            subtitle="Begin playback on the device instead of transferring it paused",
        )

    def get_extra_config_rows(self) -> list:
        self._target_group = Adw.PreferencesGroup(
            title="Target device",
            description="Spotify device IDs are not permanent. If a device stops being found, pick it again here.",
        )
        self._refresh_target_rows()
        return [self._target_group]

    def _refresh_target_rows(self) -> None:
        group = self._target_group
        if group is None:
            return

        for row in self._target_rows:
            group.remove(row)
        self._target_rows = []

        settings = self.settings()
        selected_id = settings.get("device_id") or ""
        devices = self.manager.get_devices()

        if not devices:
            row = Adw.ActionRow(
                title="No Spotify devices found",
                subtitle="Open Spotify on the device you want, then refresh.",
            )
            group.add(row)
            self._target_rows.append(row)
        else:
            for device in devices:
                row = Adw.ActionRow(title=device.name, subtitle=device.device_type)
                button = Gtk.CheckButton(active=device.id == selected_id, valign=Gtk.Align.CENTER)
                button.connect("toggled", self._on_device_chosen, device.id, device.name)
                row.add_prefix(button)
                group.add(row)
                self._target_rows.append(row)

        saved_name = settings.get("device_name")
        if selected_id and all(device.id != selected_id for device in devices):
            row = Adw.ActionRow(title=f"{saved_name or 'Saved device'} is not available", subtitle=selected_id)
            group.add(row)
            self._target_rows.append(row)

        refresh = Adw.ActionRow(title="Refresh devices", subtitle="Ask Spotify which devices exist right now.")
        button = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: (self.manager.refresh_devices(), self._refresh_target_rows()))
        refresh.add_suffix(button)
        group.add(refresh)
        self._target_rows.append(refresh)

    def _on_device_chosen(self, button, device_id: str, device_name: str) -> None:
        if not button.get_active():
            return
        settings = self.settings()
        settings["device_id"] = device_id or ""
        # The name is stored too, so an unavailable device can still be named in
        # the UI instead of showing a bare ID.
        settings["device_name"] = device_name
        self.set_settings(settings)
        self._last_signature = None
        self.render()

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_transfer",
                ui_label="Transfer playback",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        settings = self.settings()
        device_id = settings.get("device_id")
        if not device_id or self.manager.get_device_by_id(device_id) is None:
            self.report_failure()
            return

        self.manager.transfer_playback(device_id, bool(settings.get("start_playing")))
        self.flash("MOVED")

    # -- rendering --------------------------------------------------------

    def _target_device(self):
        return self.manager.get_device_by_id(self.setting("device_id"))

    def state_signature(self):
        device = self._target_device()
        return (
            self.blocking_status(),
            self.setting("device_id"),
            device.name if device else None,
            device.is_active if device else None,
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None and status is not ActionStatus.NO_DEVICE:
            return self.render_status(status)

        settings = self.settings()
        if not settings.get("device_id"):
            return self.render_status(ActionStatus.UNKNOWN, detail="PICK\nDEVICE")

        device = self._target_device()
        if device is None:
            return self.render_status(ActionStatus.NO_DEVICE, detail="DEVICE\nMISSING")

        return render_text_key(
            self.image_size(),
            [device.name],
            color=theme.WHITE,
            accent="PLAYING HERE" if device.is_active else "TRANSFER",
            accent_color=theme.SPOTIFY_GREEN if device.is_active else theme.TITLE,
        )
