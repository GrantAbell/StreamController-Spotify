"""Spotify: Previous Song and Next Song.

Both follow the reference behaviour: a short press changes track, holding seeks
in that direction and suppresses the track change. Holding repeats at a fixed
interval, so the seek feels continuous rather than one jump per press.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from .base import SpotifyActionBase
from .hold import HoldRepeater

DEFAULT_HOLD_INTERVAL_MS = 400


class _SkipActionBase(SpotifyActionBase):
    #: +1 for next/forward, -1 for previous/backward.
    DIRECTION = 1

    EXTRA_DEFAULTS = {
        "hold_seeks": True,
        "seek_seconds": 5,
        "hold_interval_ms": DEFAULT_HOLD_INTERVAL_MS,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._repeater = HoldRepeater(DEFAULT_HOLD_INTERVAL_MS / 1000.0, self._seek_once)
        self._hold_consumed = False

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._hold_row = SwitchRow(
            action_core=self,
            var_name="hold_seeks",
            default_value=True,
            title="Hold to seek",
            subtitle="Holding the key seeks instead of changing track",
        )
        self._seek_row = SpinRow(
            action_core=self,
            var_name="seek_seconds",
            default_value=5,
            min=1,
            max=120,
            step=1,
            digits=0,
            title="Seek amount",
            subtitle="Seconds per seek step while held",
        )
        self._interval_row = SpinRow(
            action_core=self,
            var_name="hold_interval_ms",
            default_value=DEFAULT_HOLD_INTERVAL_MS,
            min=100,
            max=2000,
            step=50,
            digits=0,
            title="Repeat interval",
            subtitle="Milliseconds between seek steps while held",
        )

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_skip_press",
                ui_label="Change track",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_skip_hold_start",
                ui_label="Start seeking",
                default_events=[Input.Key.Events.HOLD_START, Input.Dial.Events.HOLD_START],
                callback=self._on_hold_start,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_skip_hold_stop",
                ui_label="Stop seeking",
                default_events=[Input.Key.Events.HOLD_STOP, Input.Dial.Events.HOLD_STOP],
                callback=self._on_hold_stop,
            )
        )

    def _on_press(self, _data=None) -> None:
        # A hold already did something; the short press that follows it must not
        # also change track.
        if self._hold_consumed:
            self._hold_consumed = False
            return
        self._change_track()

    def _on_hold_start(self, _data=None) -> None:
        if not bool(self.setting("hold_seeks", True)):
            return
        self._hold_consumed = True
        self._repeater.start(self.int_setting("hold_interval_ms", DEFAULT_HOLD_INTERVAL_MS) / 1000.0)

    def _on_hold_stop(self, _data=None) -> None:
        self._repeater.stop()

    def _seek_once(self) -> None:
        seconds = max(1, self.int_setting("seek_seconds", 5))
        self.manager.seek_relative(self.DIRECTION * seconds * 1000, self.device_id)

    def _change_track(self) -> None:
        raise NotImplementedError

    # -- lifecycle --------------------------------------------------------

    def _teardown(self) -> None:
        self._repeater.stop()
        super()._teardown()

    # -- rendering --------------------------------------------------------

    def state_signature(self):
        return (self.blocking_status(),)

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)
        return render_glyph_key(self.image_size(), self.ICON, color=theme.WHITE)


class PreviousAction(_SkipActionBase):
    TITLE = "Previous"
    ICON = "previous"
    DIRECTION = -1

    def _change_track(self) -> None:
        self.manager.previous_track(self.device_id)


class NextAction(_SkipActionBase):
    TITLE = "Next"
    ICON = "next"
    DIRECTION = 1

    def _change_track(self) -> None:
        self.manager.next_track(self.device_id)
