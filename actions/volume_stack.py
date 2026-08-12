"""Spotify: Volume Stack — up, down and mute on one key.

Shows the level and the mute state rather than a static speaker, so the key is
worth its slot even when you are not pressing it.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_value_key
from ..spotify.state import ActionStatus
from . import commands
from .base import SpotifyActionBase


class VolumeStackAction(SpotifyActionBase):
    TITLE = "Volume"
    ICON = "volume_stack"

    EXTRA_DEFAULTS = {
        "short_press": commands.VOLUME_UP,
        "hold": commands.TOGGLE_MUTE,
        "volume_step": 5,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_consumed = False

    def build_action_ui(self) -> None:
        self._short_row = ComboRow(
            action_core=self,
            var_name="short_press",
            default_value=commands.VOLUME_UP,
            items=commands.combo_items(commands.VOLUME_COMMANDS),
            title="Short press",
            subtitle="What a normal press does",
        )
        self._hold_row = ComboRow(
            action_core=self,
            var_name="hold",
            default_value=commands.TOGGLE_MUTE,
            items=commands.combo_items(commands.VOLUME_COMMANDS),
            title="Hold",
            subtitle="What holding the key does",
        )
        self._step_row = SpinRow(
            action_core=self,
            var_name="volume_step",
            default_value=5,
            min=1,
            max=50,
            step=1,
            digits=0,
            title="Step",
            subtitle="Percentage points per press",
        )

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_stack_press",
                ui_label="Short press action",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_stack_hold",
                ui_label="Hold action",
                default_events=[Input.Key.Events.HOLD_START, Input.Dial.Events.HOLD_START],
                callback=self._on_hold,
            )
        )

    def _on_press(self, _data=None) -> None:
        if self._hold_consumed:
            self._hold_consumed = False
            return
        commands.run(self, self.combo_value("short_press", commands.VOLUME_UP))

    def _on_hold(self, _data=None) -> None:
        command = self.combo_value("hold", commands.TOGGLE_MUTE)
        if command == commands.NOTHING:
            return
        self._hold_consumed = True
        commands.run(self, command)

    def state_signature(self):
        return (
            self.blocking_status(),
            self.manager.get_volume(),
            self.manager.supports_volume(),
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        if not self.manager.supports_volume():
            return self.render_status(ActionStatus.UNAVAILABLE, detail="NO\nVOLUME")

        percent = self.manager.get_volume()
        muted = percent == 0

        return render_value_key(
            self.image_size(),
            "muted" if muted else "volume_stack",
            "--" if percent is None else f"{percent}%",
            color=theme.MUTED if muted else theme.SPOTIFY_GREEN,
            value_color=theme.MUTED if muted else theme.WHITE,
            fraction=None if percent is None else percent / 100.0,
            caption="MUTED" if muted else None,
        )
