"""Spotify: Playback Control — the Stream Deck+ transport dial.

Rotate to change track; hold the dial in and rotate to seek instead. That one
modifier is what makes a single dial cover both jobs, and it is why the dial's
own pressed state is tracked here from DOWN and UP.
"""

from __future__ import annotations

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering.dial import render_playback_dial
from ..spotify.state import (
    ActionStatus,
    format_duration,
    interpolated_progress_ms,
    progress_fraction,
)
from . import commands
from .base import SpotifyActionBase

CLOCKWISE_NEXT = "next"
CLOCKWISE_PREVIOUS = "previous"

#: How long the "SEEK +5s" readout stays up after the last turn.
SEEK_HINT_SECONDS = 1.5


class PlaybackDialAction(SpotifyActionBase):
    TITLE = "Playback"
    ICON = "playback_dial"
    WANTS_TICK = True
    WANTS_MARQUEE = True

    EXTRA_DEFAULTS = {
        "seek_step_seconds": 5,
        "clockwise_behavior": CLOCKWISE_NEXT,
        "touch_action": commands.PLAY_PAUSE,
        "long_touch_action": commands.LIKE_UNLIKE,
        "push_action": commands.PLAY_PAUSE,
        "seek_seconds": 5,
        "show_artwork": True,
        "show_artist": True,
        "show_progress": True,
        "marquee": True,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dial_pressed = False
        self._rotated_while_pressed = False
        self._seek_hint = None
        self._seek_hint_until = 0.0

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._direction_row = ComboRow(
            action_core=self,
            var_name="clockwise_behavior",
            default_value=CLOCKWISE_NEXT,
            items=[
                SimpleComboRowItem(CLOCKWISE_NEXT, "Clockwise: next track"),
                SimpleComboRowItem(CLOCKWISE_PREVIOUS, "Clockwise: previous track"),
            ],
            title="Rotation",
            subtitle="Which way the dial moves through the queue",
        )
        self._seek_step_row = SpinRow(
            action_core=self,
            var_name="seek_step_seconds",
            default_value=5,
            min=1,
            max=120,
            step=1,
            digits=0,
            title="Seek step",
            subtitle="Seconds per click while the dial is held in",
        )
        self._push_row = ComboRow(
            action_core=self,
            var_name="push_action",
            default_value=commands.PLAY_PAUSE,
            items=commands.combo_items(commands.PLAYBACK_COMMANDS),
            title="Dial press",
            subtitle="A press with no rotation",
        )
        self._touch_row = ComboRow(
            action_core=self,
            var_name="touch_action",
            default_value=commands.PLAY_PAUSE,
            items=commands.combo_items(commands.PLAYBACK_COMMANDS),
            title="Screen tap",
            subtitle="Tapping the dial's part of the touchscreen",
        )
        self._long_touch_row = ComboRow(
            action_core=self,
            var_name="long_touch_action",
            default_value=commands.LIKE_UNLIKE,
            items=commands.combo_items(commands.PLAYBACK_COMMANDS),
            title="Long screen tap",
            subtitle="Holding a tap on the touchscreen",
        )
        self._artwork_row = SwitchRow(
            action_core=self,
            var_name="show_artwork",
            default_value=True,
            title="Album artwork",
            on_change=self._on_display_changed,
        )
        self._artist_row = SwitchRow(
            action_core=self,
            var_name="show_artist",
            default_value=True,
            title="Artist",
            on_change=self._on_display_changed,
        )
        self._progress_row = SwitchRow(
            action_core=self,
            var_name="show_progress",
            default_value=True,
            title="Progress bar",
            on_change=self._on_display_changed,
        )
        self._marquee_row = SwitchRow(
            action_core=self,
            var_name="marquee",
            default_value=True,
            title="Scroll long titles",
            on_change=self._on_display_changed,
        )

    def _on_display_changed(self, _widget, _new_value, _old_value) -> None:
        self._last_signature = None
        self.render()

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_cw",
                ui_label="Turn clockwise",
                default_events=[Input.Dial.Events.TURN_CW],
                callback=lambda data=None: self._on_turn(clockwise=True),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_ccw",
                ui_label="Turn counter-clockwise",
                default_events=[Input.Dial.Events.TURN_CCW],
                callback=lambda data=None: self._on_turn(clockwise=False),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_down",
                ui_label="Dial pressed down",
                default_events=[Input.Dial.Events.DOWN, Input.Key.Events.DOWN],
                callback=self._on_down,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_up",
                ui_label="Dial released",
                default_events=[Input.Dial.Events.UP, Input.Key.Events.UP],
                callback=self._on_up,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_touch",
                ui_label="Screen tap action",
                default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS],
                callback=lambda data=None: commands.run(self, self.combo_value("touch_action", commands.PLAY_PAUSE)),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_playback_dial_long_touch",
                ui_label="Long screen tap action",
                default_events=[Input.Dial.Events.LONG_TOUCH_PRESS],
                callback=lambda data=None: commands.run(self, self.combo_value("long_touch_action", commands.LIKE_UNLIKE)),
            )
        )

    def _on_down(self, _data=None) -> None:
        self._dial_pressed = True
        self._rotated_while_pressed = False

    def _on_up(self, _data=None) -> None:
        was_pressed = self._dial_pressed
        self._dial_pressed = False

        # A press that turned the dial was a seek gesture, so it must not also
        # trigger the press action on release.
        if was_pressed and not self._rotated_while_pressed:
            commands.run(self, self.combo_value("push_action", commands.PLAY_PAUSE))

        self._rotated_while_pressed = False

    def _on_turn(self, clockwise: bool) -> None:
        if self._dial_pressed:
            self._rotated_while_pressed = True
            self._seek(forward=clockwise)
            return

        reversed_direction = self.combo_value("clockwise_behavior", CLOCKWISE_NEXT) == CLOCKWISE_PREVIOUS
        go_next = clockwise != reversed_direction

        if go_next:
            self.manager.next_track(self.device_id)
        else:
            self.manager.previous_track(self.device_id)

    def _seek(self, forward: bool) -> None:
        import time

        step = max(1, self.int_setting("seek_step_seconds", 5))
        delta_ms = step * 1000 * (1 if forward else -1)
        self.manager.seek_relative(delta_ms, self.device_id)

        self._seek_hint = f"SEEK {'+' if forward else '−'}{step}s"
        self._seek_hint_until = time.monotonic() + SEEK_HINT_SECONDS
        self._last_signature = None
        self.render()

    # -- rendering --------------------------------------------------------

    def _active_seek_hint(self) -> str | None:
        import time

        if self._seek_hint and time.monotonic() < self._seek_hint_until:
            return self._seek_hint
        self._seek_hint = None
        return None

    def state_signature(self):
        # Progress and the marquee both move without a state change.
        return None

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        track = state.track

        if track is None:
            # Idle rather than dead: show the device a press would wake.
            device = self.manager.target_device()
            if device is None:
                return self.render_status(ActionStatus.NO_DEVICE, detail="NOTHING\nPLAYING")
            return render_playback_dial(
                self.image_size(),
                track_name=device.name,
                artist="Press to play",
                is_playing=False,
                fraction=None,
                show_artist=True,
                show_progress=False,
            ).image

        settings = self.settings()
        artwork = self.manager.artwork.get(track.artwork_url) if settings.get("show_artwork", True) else None

        result = render_playback_dial(
            self.image_size(),
            track_name=track.name,
            artist=track.artist_text,
            is_playing=state.is_playing,
            fraction=progress_fraction(state),
            position_text=format_duration(interpolated_progress_ms(state)),
            duration_text=format_duration(state.duration_ms),
            artwork=artwork,
            seek_hint=self._active_seek_hint(),
            show_artist=bool(settings.get("show_artist", True)),
            show_progress=bool(settings.get("show_progress", True)),
            title_offset=self.marquee_offset(),
        )

        self.update_marquee(result.title_overflow, track.uri or track.name)
        return result.image
