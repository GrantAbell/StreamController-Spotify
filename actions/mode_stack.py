"""Spotify: Mode Stack — shuffle and both repeat modes on one key.

The rendering always shows all three states regardless of how press and hold are
mapped, because the point of combining them is to see them, not just to change
them.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.ComboRow import ComboRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering.key import render_mode_stack_key
from . import commands
from .base import SpotifyActionBase


class ModeStackAction(SpotifyActionBase):
    TITLE = "Modes"
    ICON = "mode_stack"

    EXTRA_DEFAULTS = {
        "short_press": commands.CYCLE_REPEAT,
        "hold": commands.TOGGLE_SHUFFLE,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_consumed = False

    def build_action_ui(self) -> None:
        self._short_row = ComboRow(
            action_core=self,
            var_name="short_press",
            default_value=commands.CYCLE_REPEAT,
            items=commands.combo_items(commands.MODE_COMMANDS),
            title="Short press",
            subtitle="What a normal press does",
        )
        self._hold_row = ComboRow(
            action_core=self,
            var_name="hold",
            default_value=commands.TOGGLE_SHUFFLE,
            items=commands.combo_items(commands.MODE_COMMANDS),
            title="Hold",
            subtitle="What holding the key does",
        )

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_mode_stack_press",
                ui_label="Short press action",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_mode_stack_hold",
                ui_label="Hold action",
                default_events=[Input.Key.Events.HOLD_START, Input.Dial.Events.HOLD_START],
                callback=self._on_hold,
            )
        )

    def _on_press(self, _data=None) -> None:
        if self._hold_consumed:
            self._hold_consumed = False
            return
        commands.run(self, self.combo_value("short_press", commands.CYCLE_REPEAT))

    def _on_hold(self, _data=None) -> None:
        command = self.combo_value("hold", commands.TOGGLE_SHUFFLE)
        if command == commands.NOTHING:
            return
        self._hold_consumed = True
        commands.run(self, command)

    def state_signature(self):
        state = self.manager.get_playback_state()
        return (self.blocking_status(), state.shuffle, state.is_smart_shuffle, state.repeat_mode)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        return render_mode_stack_key(
            self.image_size(),
            shuffle=state.shuffle,
            repeat_mode=state.repeat_mode,
            smart_shuffle=state.is_smart_shuffle,
        )
