"""Spotify: Song Stack — the now-playing surface in one key slot.

Artwork, title, artist, transport state, progress, liked and explicit, with a
configurable press and hold. Everything it draws comes from state the manager
already holds; the only extra work it causes is the artwork fetch, which is
shared with every other action showing the same album.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.SpinRow import SpinRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.key import render_glyph_key
from ..rendering.song import render_song_stack
from ..spotify.state import ActionStatus, progress_fraction
from . import commands
from .base import SpotifyActionBase


class SongStackAction(SpotifyActionBase):
    TITLE = "Now playing"
    ICON = "song_stack"
    WANTS_TICK = True
    WANTS_MARQUEE = True

    EXTRA_DEFAULTS = {
        "primary_action": commands.PLAY_PAUSE,
        "hold_action": commands.LIKE_UNLIKE,
        "seek_seconds": 5,
        "playlist_id": "",
        "show_artwork": True,
        "show_title": True,
        "show_artist": True,
        "show_progress": True,
        "show_like_state": True,
        "show_explicit": True,
        "marquee": True,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_consumed = False

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._primary_row = ComboRow(
            action_core=self,
            var_name="primary_action",
            default_value=commands.PLAY_PAUSE,
            items=commands.combo_items(commands.PLAYBACK_COMMANDS),
            title="Short press",
            subtitle="What a normal press does",
        )
        self._hold_row = ComboRow(
            action_core=self,
            var_name="hold_action",
            default_value=commands.LIKE_UNLIKE,
            items=commands.combo_items(commands.PLAYBACK_COMMANDS),
            title="Hold",
            subtitle="What holding the key does",
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
            subtitle="Seconds, when a seek command is chosen above",
        )

        for var_name, title, subtitle in (
            ("show_artwork", "Album artwork", "Shown uncropped in its own area"),
            ("show_title", "Track title", None),
            ("show_artist", "Artist", None),
            ("show_progress", "Progress bar", None),
            ("show_like_state", "Liked state", "Small marker in the top row"),
            ("show_explicit", "Explicit marker", None),
            ("marquee", "Scroll long text", "Titles too wide to fit scroll gently"),
        ):
            setattr(
                self,
                f"_{var_name}_row",
                SwitchRow(
                    action_core=self,
                    var_name=var_name,
                    default_value=True,
                    title=title,
                    subtitle=subtitle,
                    on_change=self._on_display_changed,
                ),
            )

    def _on_display_changed(self, _widget, _new_value, _old_value) -> None:
        self._last_signature = None
        self.render()

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_song_stack_press",
                ui_label="Short press action",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_song_stack_hold",
                ui_label="Hold action",
                default_events=[Input.Key.Events.HOLD_START, Input.Dial.Events.HOLD_START],
                callback=self._on_hold,
            )
        )

    def _on_press(self, _data=None) -> None:
        if self._hold_consumed:
            self._hold_consumed = False
            return
        commands.run(self, self.combo_value("primary_action", commands.PLAY_PAUSE))

    def _on_hold(self, _data=None) -> None:
        command = self.combo_value("hold_action", commands.LIKE_UNLIKE)
        if command == commands.NOTHING:
            return
        self._hold_consumed = True
        commands.run(self, command)

    # -- rendering --------------------------------------------------------

    def state_signature(self):
        # Deliberately None: the progress bar and the marquee both change
        # without any state change, and this action redraws on tick.
        return None

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        state = self.manager.get_playback_state()
        track = state.track

        if track is None:
            # Spotify open but idle: Spotify reports no track at all, so name
            # the device a press would start instead of looking broken.
            device = self.manager.target_device()
            if device is not None:
                return render_glyph_key(
                    self.image_size(),
                    "play",
                    color=theme.SPOTIFY_GREEN,
                    caption=device.name[:12],
                )
            return self.render_status(ActionStatus.NO_DEVICE, detail="NOTHING\nPLAYING")

        settings = self.settings()
        show_artwork = bool(settings.get("show_artwork", True))
        artwork = self.manager.artwork.get(track.artwork_url) if show_artwork else None

        offset = self.marquee_offset()

        result = render_song_stack(
            self.image_size(),
            track_name=track.name,
            artist=track.artist_text,
            artwork=artwork,
            is_playing=state.is_playing,
            fraction=progress_fraction(state),
            like_state=self.manager.get_like_state().value,
            explicit=track.explicit,
            show_artwork=show_artwork,
            show_title=bool(settings.get("show_title", True)),
            show_artist=bool(settings.get("show_artist", True)),
            show_progress=bool(settings.get("show_progress", True)),
            show_like_state=bool(settings.get("show_like_state", True)),
            show_explicit=bool(settings.get("show_explicit", True)),
            title_offset=offset,
            artist_offset=offset,
        )

        self.update_marquee(max(result.title_overflow, result.artist_overflow), track.uri or track.name)
        return result.image
