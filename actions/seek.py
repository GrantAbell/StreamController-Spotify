"""Spotify: Backward Seek and Forward Seek.

Both seek from the locally interpolated position rather than reading playback
state first, so a press costs one request instead of two and stays responsive
when Spotify is slow.
"""

from __future__ import annotations

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from .base import SpotifyActionBase

PRESET_SECONDS = ("1", "5", "10", "15", "30")
CUSTOM = "custom"


class _SeekActionBase(SpotifyActionBase):
    DIRECTION = 1

    EXTRA_DEFAULTS = {
        "seek_preset": "5",
        "seek_seconds": 5,
    }

    def build_action_ui(self) -> None:
        self._preset_row = ComboRow(
            action_core=self,
            var_name="seek_preset",
            default_value="5",
            items=[SimpleComboRowItem(value, f"{value} seconds") for value in PRESET_SECONDS]
            + [SimpleComboRowItem(CUSTOM, "Custom")],
            title="Seek amount",
            subtitle="How far each press moves",
            on_change=self._on_preset_changed,
        )
        self._custom_row = SpinRow(
            action_core=self,
            var_name="seek_seconds",
            default_value=5,
            min=1,
            max=600,
            step=1,
            digits=0,
            title="Custom amount",
            subtitle="Seconds, used when the amount above is set to Custom",
        )

    def _on_preset_changed(self, _widget, _new_value, _old_value) -> None:
        self._last_signature = None
        self.render()

    def seek_seconds(self) -> int:
        preset = self.combo_value("seek_preset", "5")
        if preset == CUSTOM:
            return max(1, self.int_setting("seek_seconds", 5))
        try:
            return max(1, int(preset))
        except ValueError:
            return 5

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_seek",
                ui_label="Seek",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        self.manager.seek_relative(self.DIRECTION * self.seek_seconds() * 1000, self.device_id)

    def state_signature(self):
        return (self.blocking_status(), self.seek_seconds())

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        seconds = self.seek_seconds()
        return render_glyph_key(
            self.image_size(),
            self.ICON,
            color=theme.WHITE,
            caption=f"{'+' if self.DIRECTION > 0 else '−'}{seconds}s",
        )


class BackwardSeekAction(_SeekActionBase):
    TITLE = "Rewind"
    ICON = "seek_backward"
    DIRECTION = -1


class ForwardSeekAction(_SeekActionBase):
    TITLE = "Forward"
    ICON = "seek_forward"
    DIRECTION = 1
