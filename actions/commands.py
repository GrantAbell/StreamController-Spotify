"""The one place a "what should this press do?" setting is turned into an act.

Song Stack, Mode Stack, Volume Stack and the dials all let the user remap their
press and hold, and they all offer overlapping choices. Defining each command
once here means adding a command makes it available everywhere, and no two
actions can implement "seek forward" slightly differently.
"""

from __future__ import annotations

from typing import Callable

from ..spotify.models import REPEAT_CONTEXT, REPEAT_TRACK
from ..spotify.state import next_repeat_mode, toggle_repeat_mode

NOTHING = "nothing"
PLAY_PAUSE = "play_pause"
PREVIOUS = "previous"
NEXT = "next"
SEEK_BACKWARD = "seek_backward"
SEEK_FORWARD = "seek_forward"
LIKE_UNLIKE = "like_unlike"
TOGGLE_SHUFFLE = "toggle_shuffle"
CYCLE_REPEAT = "cycle_repeat"
TOGGLE_CONTEXT_REPEAT = "toggle_context_repeat"
TOGGLE_TRACK_REPEAT = "toggle_track_repeat"
VOLUME_UP = "volume_up"
VOLUME_DOWN = "volume_down"
TOGGLE_MUTE = "toggle_mute"
ADD_TO_PLAYLIST = "add_to_playlist"
COPY_SONG_INFO = "copy_song_info"
OPEN_IN_SPOTIFY = "open_in_spotify"

LABELS = {
    NOTHING: "Do nothing",
    PLAY_PAUSE: "Play / Pause",
    PREVIOUS: "Previous",
    NEXT: "Next",
    SEEK_BACKWARD: "Backward seek",
    SEEK_FORWARD: "Forward seek",
    LIKE_UNLIKE: "Like / Unlike",
    TOGGLE_SHUFFLE: "Toggle shuffle",
    CYCLE_REPEAT: "Cycle repeat",
    TOGGLE_CONTEXT_REPEAT: "Toggle context repeat",
    TOGGLE_TRACK_REPEAT: "Toggle track repeat",
    VOLUME_UP: "Volume up",
    VOLUME_DOWN: "Volume down",
    TOGGLE_MUTE: "Mute / Unmute",
    ADD_TO_PLAYLIST: "Add to playlist",
    COPY_SONG_INFO: "Copy song info",
    OPEN_IN_SPOTIFY: "Open current item in Spotify",
}

#: What Song Stack and the dials offer.
PLAYBACK_COMMANDS = (
    PLAY_PAUSE,
    PREVIOUS,
    NEXT,
    SEEK_BACKWARD,
    SEEK_FORWARD,
    LIKE_UNLIKE,
    TOGGLE_SHUFFLE,
    CYCLE_REPEAT,
    ADD_TO_PLAYLIST,
    COPY_SONG_INFO,
    OPEN_IN_SPOTIFY,
    NOTHING,
)

#: What Mode Stack offers.
MODE_COMMANDS = (
    CYCLE_REPEAT,
    TOGGLE_SHUFFLE,
    TOGGLE_CONTEXT_REPEAT,
    TOGGLE_TRACK_REPEAT,
    NOTHING,
)

#: What Volume Stack offers.
VOLUME_COMMANDS = (VOLUME_UP, VOLUME_DOWN, TOGGLE_MUTE, NOTHING)


def run(action, command: str) -> None:
    """Perform `command` on behalf of `action`. Always returns immediately."""
    handler: Callable | None = _HANDLERS.get(command)
    if handler is None:
        return
    handler(action)


def _seek_ms(action, forward: bool) -> int:
    seconds = action.int_setting("seek_seconds", action.plugin_base.default_seek_seconds())
    seconds = max(1, seconds)
    return seconds * 1000 * (1 if forward else -1)


def _volume_step(action) -> int:
    return max(1, action.int_setting("volume_step", action.plugin_base.default_volume_step()))


def _play_pause(action) -> None:
    action.manager.toggle_playback(action.device_id)


def _previous(action) -> None:
    action.manager.previous_track(action.device_id)


def _next(action) -> None:
    action.manager.next_track(action.device_id)


def _seek_backward(action) -> None:
    action.manager.seek_relative(_seek_ms(action, forward=False), action.device_id)


def _seek_forward(action) -> None:
    action.manager.seek_relative(_seek_ms(action, forward=True), action.device_id)


def _like(action) -> None:
    action.manager.toggle_like(on_result=lambda liked: action.flash("LIKED" if liked else "REMOVED"))


def _toggle_shuffle(action) -> None:
    action.manager.toggle_shuffle(action.device_id)


def _cycle_repeat(action) -> None:
    current = action.manager.get_playback_state().repeat_mode
    action.manager.set_repeat(next_repeat_mode(current), action.device_id)


def _toggle_context_repeat(action) -> None:
    current = action.manager.get_playback_state().repeat_mode
    action.manager.set_repeat(toggle_repeat_mode(current, REPEAT_CONTEXT), action.device_id)


def _toggle_track_repeat(action) -> None:
    current = action.manager.get_playback_state().repeat_mode
    action.manager.set_repeat(toggle_repeat_mode(current, REPEAT_TRACK), action.device_id)


def _volume_up(action) -> None:
    action.manager.adjust_volume(_volume_step(action), action.device_id)


def _volume_down(action) -> None:
    action.manager.adjust_volume(-_volume_step(action), action.device_id)


def _toggle_mute(action) -> None:
    action.manager.toggle_mute(action.device_id)


def _add_to_playlist(action) -> None:
    playlist_id = action.setting("playlist_id", "")
    if not playlist_id:
        action.report_failure()
        return
    action.manager.add_current_to_playlist(
        playlist_id,
        on_result=lambda ok: action.flash("ADDED") if ok else action.report_failure(),
    )


def _copy_song_info(action) -> None:
    from .clipboard import copy_current_song

    copy_current_song(action)


def _open_in_spotify(action) -> None:
    state = action.manager.get_playback_state()
    target = state.track.external_url or state.track.uri if state.track else None
    if action.manager.open_in_spotify(target):
        action.flash("OPENED")
    else:
        action.report_failure()


_HANDLERS: dict[str, Callable] = {
    NOTHING: lambda action: None,
    PLAY_PAUSE: _play_pause,
    PREVIOUS: _previous,
    NEXT: _next,
    SEEK_BACKWARD: _seek_backward,
    SEEK_FORWARD: _seek_forward,
    LIKE_UNLIKE: _like,
    TOGGLE_SHUFFLE: _toggle_shuffle,
    CYCLE_REPEAT: _cycle_repeat,
    TOGGLE_CONTEXT_REPEAT: _toggle_context_repeat,
    TOGGLE_TRACK_REPEAT: _toggle_track_repeat,
    VOLUME_UP: _volume_up,
    VOLUME_DOWN: _volume_down,
    TOGGLE_MUTE: _toggle_mute,
    ADD_TO_PLAYLIST: _add_to_playlist,
    COPY_SONG_INFO: _copy_song_info,
    OPEN_IN_SPOTIFY: _open_in_spotify,
}


def combo_items(command_ids):
    """SimpleComboRowItems for a set of commands, in the order given."""
    from GtkHelper.ComboRow import SimpleComboRowItem

    return [SimpleComboRowItem(command_id, LABELS[command_id]) for command_id in command_ids]
