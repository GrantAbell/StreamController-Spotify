"""Spotify: Volume Control — the Stream Deck+ volume dial.

Rotation is optimistic: the level on screen follows your hand immediately while
the manager coalesces the requests behind it, so a fast spin feels continuous
instead of arriving in steps a second later.

Pushing and holding the dial is a momentary mute, which is a different gesture
from the tap that toggles mute.
"""

from __future__ import annotations

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering.dial import render_volume_dial
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase

PUSH_HOLD_MUTE = "hold_mute"
PUSH_TOGGLE_MUTE = "toggle_mute"
PUSH_NOTHING = "nothing"

TOUCH_TOGGLE_MUTE = "toggle_mute"
TOUCH_NOTHING = "nothing"


class VolumeDialAction(SpotifyActionBase):
    TITLE = "Volume"
    ICON = "volume_dial"

    EXTRA_DEFAULTS = {
        "volume_step": 5,
        "push_behavior": PUSH_HOLD_MUTE,
        "touch_behavior": TOUCH_TOGGLE_MUTE,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_restore: int | None = None
        self._hold_device: str | None = None

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._step_row = SpinRow(
            action_core=self,
            var_name="volume_step",
            default_value=5,
            min=1,
            max=25,
            step=1,
            digits=0,
            title="Step",
            subtitle="Percentage points per click",
        )
        self._push_row = ComboRow(
            action_core=self,
            var_name="push_behavior",
            default_value=PUSH_HOLD_MUTE,
            items=[
                SimpleComboRowItem(PUSH_HOLD_MUTE, "Mute while held"),
                SimpleComboRowItem(PUSH_TOGGLE_MUTE, "Toggle mute"),
                SimpleComboRowItem(PUSH_NOTHING, "Do nothing"),
            ],
            title="Dial press",
            subtitle="What pushing the dial in does",
        )
        self._touch_row = ComboRow(
            action_core=self,
            var_name="touch_behavior",
            default_value=TOUCH_TOGGLE_MUTE,
            items=[
                SimpleComboRowItem(TOUCH_TOGGLE_MUTE, "Toggle mute"),
                SimpleComboRowItem(TOUCH_NOTHING, "Do nothing"),
            ],
            title="Screen tap",
            subtitle="Tapping the dial's part of the touchscreen",
        )

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_dial_cw",
                ui_label="Volume up",
                default_events=[Input.Dial.Events.TURN_CW],
                callback=lambda data=None: self._on_turn(1),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_dial_ccw",
                ui_label="Volume down",
                default_events=[Input.Dial.Events.TURN_CCW],
                callback=lambda data=None: self._on_turn(-1),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_dial_down",
                ui_label="Dial pressed down",
                default_events=[Input.Dial.Events.DOWN, Input.Key.Events.DOWN],
                callback=self._on_down,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_dial_up",
                ui_label="Dial released",
                default_events=[Input.Dial.Events.UP, Input.Key.Events.UP],
                callback=self._on_up,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_volume_dial_touch",
                ui_label="Screen tap action",
                default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS],
                callback=self._on_touch,
            )
        )

    def _on_turn(self, direction: int) -> None:
        if not self.manager.supports_volume():
            return
        step = max(1, self.int_setting("volume_step", 5))
        self.manager.adjust_volume(direction * step, self.device_id)

    def _on_down(self, _data=None) -> None:
        behavior = self.combo_value("push_behavior", PUSH_HOLD_MUTE)
        if behavior != PUSH_HOLD_MUTE:
            return

        state = self.manager.get_playback_state()
        self._hold_device = state.device.id if state.device else None
        self._hold_restore = self.manager.begin_hold_mute(self.device_id)

    def _on_up(self, _data=None) -> None:
        behavior = self.combo_value("push_behavior", PUSH_HOLD_MUTE)

        if behavior == PUSH_TOGGLE_MUTE:
            self.manager.toggle_mute(self.device_id)
            return

        if behavior != PUSH_HOLD_MUTE:
            return

        restore, self._hold_restore = self._hold_restore, None
        device_at_press, self._hold_device = self._hold_device, None
        self.manager.end_hold_mute(restore, self.device_id, device_at_press=device_at_press)

    def _on_touch(self, _data=None) -> None:
        if self.combo_value("touch_behavior", TOUCH_TOGGLE_MUTE) == TOUCH_TOGGLE_MUTE:
            self.manager.toggle_mute(self.device_id)

    # -- rendering --------------------------------------------------------

    def state_signature(self):
        return (
            self.blocking_status(),
            self.manager.get_volume(),
            self.manager.supports_volume(),
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None and status is not ActionStatus.UNAVAILABLE:
            return self.render_status(status)

        supported = self.manager.supports_volume()
        percent = self.manager.get_volume()

        return render_volume_dial(
            self.image_size(),
            percent=percent,
            muted=percent == 0,
            supports_volume=supported,
        )
