"""Spotify: Volume Up, Volume Down, Mute / Unmute and Set Volume.

All four read the same optimistic level from the manager, so a row of volume
keys stays in agreement while requests are still in flight, and a device that
reports no volume support says so instead of sending commands nothing will act
on.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.ScaleRow import ScaleRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_value_key
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class _VolumeActionBase(SpotifyActionBase):
    TITLE = "Volume"
    ICON = "volume"

    def volume_percent(self) -> int | None:
        return self.manager.get_volume()

    def supports_volume(self) -> bool:
        # Asks about the device a command would reach, which may be an idle one.
        return self.manager.supports_volume()

    def state_signature(self):
        return (self.blocking_status(), self.volume_percent(), self.supports_volume(), self._extra_signature())

    def _extra_signature(self):
        return None

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        if not self.supports_volume():
            return self.render_status(ActionStatus.UNAVAILABLE, detail="NO\nVOLUME")

        percent = self.volume_percent()
        muted = percent == 0
        return render_value_key(
            self.image_size(),
            "muted" if muted else self.ICON,
            "--" if percent is None else f"{percent}%",
            color=theme.MUTED if muted else theme.SPOTIFY_GREEN,
            value_color=theme.MUTED if muted else theme.WHITE,
            fraction=None if percent is None else percent / 100.0,
        )


class _StepVolumeAction(_VolumeActionBase):
    DIRECTION = 1
    EXTRA_DEFAULTS = {"volume_step": 5}

    def build_action_ui(self) -> None:
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
                id="spotify_volume_step",
                ui_label="Change volume",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        step = max(1, self.int_setting("volume_step", 5))
        self.manager.adjust_volume(self.DIRECTION * step, self.device_id)


class VolumeUpAction(_StepVolumeAction):
    ICON = "volume_up"
    DIRECTION = 1


class VolumeDownAction(_StepVolumeAction):
    ICON = "volume_down"
    DIRECTION = -1


class MuteAction(_VolumeActionBase):
    TITLE = "Mute"
    ICON = "volume"

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_mute",
                ui_label="Mute / Unmute",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        self.manager.toggle_mute(self.device_id)


class SetVolumeAction(_VolumeActionBase):
    TITLE = "Set volume"
    ICON = "volume"
    EXTRA_DEFAULTS = {"target_volume": 50}

    def build_action_ui(self) -> None:
        self._target_row = ScaleRow(
            action_core=self,
            var_name="target_volume",
            default_value=50,
            min=0,
            max=100,
            step=1,
            digits=0,
            title="Volume",
            subtitle="The level this key sets",
        )

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_set_volume",
                ui_label="Set volume",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        self.manager.set_volume(self.int_setting("target_volume", 50), self.device_id)

    def _extra_signature(self):
        return self.int_setting("target_volume", 50)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        if not self.supports_volume():
            return self.render_status(ActionStatus.UNAVAILABLE, detail="NO\nVOLUME")

        target = self.int_setting("target_volume", 50)
        current = self.volume_percent()
        # The key shows the level it sets; the bar shows where the device
        # actually is, so you can see whether pressing would change anything.
        return render_value_key(
            self.image_size(),
            "volume",
            f"{target}%",
            color=theme.SPOTIFY_GREEN,
            fraction=None if current is None else current / 100.0,
            caption="SET",
        )
