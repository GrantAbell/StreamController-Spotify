"""Deck Essentials for Spotify — plugin entry point.

Owns exactly one SpotifyManager and one MarqueeScheduler for the whole plugin.
Every action talks to those; none of them polls Spotify, opens a connection, or
starts a thread of its own.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

import globals as gl
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.PluginManager.PluginBase import PluginBase
from src.Signals import Signals

from .actions.add_to_playlist import AddToPlaylistAction
from .actions.browse_dial import LikedSongsDialAction, PlaylistsDialAction
from .actions.context_info import ContextInfoAction
from .actions.explicit import ExplicitAction
from .actions.like import LikeAction
from .actions.mode_stack import ModeStackAction
from .actions.play_context import PlayContextAction
from .actions.play_pause import PlayPauseAction
from .actions.playback_dial import PlaybackDialAction
from .actions.repeat import LoopContextAction, LoopSongAction
from .actions.seek import BackwardSeekAction, ForwardSeekAction
from .actions.setup import SetupAction
from .actions.shuffle import ShuffleAction
from .actions.skip import NextAction, PreviousAction
from .actions.song_clipboard import SongClipboardAction
from .actions.song_stack import SongStackAction
from .actions.transfer_playback import TransferPlaybackAction
from .actions.user_info import UserInfoAction
from .actions.volume import MuteAction, SetVolumeAction, VolumeDownAction, VolumeUpAction
from .actions.volume_dial import VolumeDialAction
from .actions.volume_stack import VolumeStackAction
from .rendering.marquee import MarqueeScheduler
from .spotify.log import log, set_debug_logging
from .spotify.manager import SpotifyManager

PLUGIN_VERSION = "1.0.0"
APP_VERSION = "1.5.0-beta.15"

SUPPORTED = ActionInputSupport.SUPPORTED
UNSUPPORTED = ActionInputSupport.UNSUPPORTED

#: Keys and dial-as-button: everything that is fundamentally a press.
KEY_SUPPORT = {Input.Key: SUPPORTED, Input.Dial: SUPPORTED, Input.Touchscreen: UNSUPPORTED}

#: The four actions designed for the Stream Deck+ dial display and rotation.
DIAL_SUPPORT = {Input.Key: UNSUPPORTED, Input.Dial: SUPPORTED, Input.Touchscreen: UNSUPPORTED}

#: (id suffix, class, visible name, icon asset, input support)
ACTIONS = (
    ("Setup", SetupAction, "Spotify: Setup", "setup", KEY_SUPPORT),
    ("PlayPause", PlayPauseAction, "Spotify: Play / Pause", "play", KEY_SUPPORT),
    ("Previous", PreviousAction, "Spotify: Previous Song", "previous", KEY_SUPPORT),
    ("Next", NextAction, "Spotify: Next Song", "next", KEY_SUPPORT),
    ("BackwardSeek", BackwardSeekAction, "Spotify: Backward Seek", "seek_backward", KEY_SUPPORT),
    ("ForwardSeek", ForwardSeekAction, "Spotify: Forward Seek", "seek_forward", KEY_SUPPORT),
    ("Shuffle", ShuffleAction, "Spotify: Shuffle", "shuffle", KEY_SUPPORT),
    ("RepeatContext", LoopContextAction, "Spotify: Loop Context", "repeat_context", KEY_SUPPORT),
    ("RepeatTrack", LoopSongAction, "Spotify: Loop Song", "repeat_track", KEY_SUPPORT),
    ("ModeStack", ModeStackAction, "Spotify: Mode Stack", "mode_stack", KEY_SUPPORT),
    ("Like", LikeAction, "Spotify: Like / Unlike", "library_add", KEY_SUPPORT),
    ("Explicit", ExplicitAction, "Spotify: Explicit Indicator", "explicit", KEY_SUPPORT),
    ("VolumeUp", VolumeUpAction, "Spotify: Volume Up", "volume_up", KEY_SUPPORT),
    ("VolumeDown", VolumeDownAction, "Spotify: Volume Down", "volume_down", KEY_SUPPORT),
    ("Mute", MuteAction, "Spotify: Volume Mute / Unmute", "muted", KEY_SUPPORT),
    ("SetVolume", SetVolumeAction, "Spotify: Set Volume", "volume", KEY_SUPPORT),
    ("VolumeStack", VolumeStackAction, "Spotify: Volume Stack", "volume_stack", KEY_SUPPORT),
    ("SongStack", SongStackAction, "Spotify: Song Stack", "song_stack", KEY_SUPPORT),
    ("SongClipboard", SongClipboardAction, "Spotify: Song Clipboard", "clipboard", KEY_SUPPORT),
    ("ContextInfo", ContextInfoAction, "Spotify: Context Information", "context", KEY_SUPPORT),
    ("TransferPlayback", TransferPlaybackAction, "Spotify: Transfer Playback", "device_transfer", KEY_SUPPORT),
    ("UserInfo", UserInfoAction, "Spotify: User Information", "user", KEY_SUPPORT),
    ("PlayContext", PlayContextAction, "Spotify: Play Context", "play_context", KEY_SUPPORT),
    ("AddToPlaylist", AddToPlaylistAction, "Spotify: Add to Playlist", "add_to_playlist", KEY_SUPPORT),
    ("PlaybackDial", PlaybackDialAction, "Spotify: Playback Control", "playback_dial", DIAL_SUPPORT),
    ("VolumeDial", VolumeDialAction, "Spotify: Volume Control", "volume_dial", DIAL_SUPPORT),
    ("PlaylistsDial", PlaylistsDialAction, "Spotify: My Playlists", "playlist_dial", DIAL_SUPPORT),
    ("LikedSongsDial", LikedSongsDialAction, "Spotify: My Liked Songs", "liked_songs_dial", DIAL_SUPPORT),
)

DEFAULT_PLUGIN_SETTINGS = {
    "schema_version": 1,
    "spotify_client_id": "",
    "callback_port": 8888,
    "playback_poll_interval_ms": 1000,
    "device_refresh_interval_ms": 15000,
    "default_volume_step": 5,
    "default_seek_seconds": 5,
    "marquee_enabled": True,
    "marquee_speed_px_per_second": 32,
    "debug_logging": False,
}


class SpotifyEssentialsPlugin(PluginBase):
    def __init__(self):
        super().__init__()

        self.has_plugin_settings = True

        self.marquee = MarqueeScheduler()
        self.spotify = SpotifyManager(
            token_path=self._token_path(),
            settings_provider=self.plugin_settings,
            marquee=self.marquee,
        )
        self._settings_area = None
        # One long-lived listener keeps the settings window's status rows in
        # step; registering per window would leak a listener each time one is
        # opened.
        self.spotify.add_listener(self._refresh_settings_ui, {"auth"})

        self.apply_debug_logging()
        self.apply_marquee_settings()

        gl.signal_manager.connect_signal(Signals.AppQuit, self._on_quit)

        for suffix, action_core, name, icon_name, support in ACTIONS:
            self.add_action_holder(
                ActionHolder(
                    plugin_base=self,
                    action_core=action_core,
                    action_id_suffix=suffix,
                    action_name=name,
                    action_support=support,
                    icon=self._action_icon(icon_name),
                )
            )

        self.register(
            plugin_name="Deck Essentials for Spotify",
            github_repo="https://github.com/GrantAbell/StreamController-Spotify",
            plugin_version=PLUGIN_VERSION,
            app_version=APP_VERSION,
        )

        log.info(
            f"Deck Essentials for Spotify: registered {len(self.action_holders)} actions "
            f"(registered={self.registered})"
        )

    # -- paths and settings -----------------------------------------------

    def _token_path(self) -> str:
        """Beside the plugin's settings file, and never inside a page."""
        return os.path.join(os.path.dirname(self.settings_path), "spotify-auth.json")

    def plugin_settings(self) -> dict:
        merged = dict(DEFAULT_PLUGIN_SETTINGS)
        merged.update(self.get_settings() or {})
        return merged

    def plugin_setting(self, key: str, fallback=None):
        return self.plugin_settings().get(key, fallback)

    def set_plugin_setting(self, key: str, value) -> None:
        settings = self.get_settings() or {}
        settings[key] = value
        self.set_settings(settings)

    def default_volume_step(self) -> int:
        try:
            return int(self.plugin_setting("default_volume_step", 5))
        except (TypeError, ValueError):
            return 5

    def default_seek_seconds(self) -> int:
        try:
            return int(self.plugin_setting("default_seek_seconds", 5))
        except (TypeError, ValueError):
            return 5

    def apply_debug_logging(self) -> None:
        set_debug_logging(bool(self.plugin_setting("debug_logging", False)))

    def apply_marquee_settings(self) -> None:
        self.marquee.configure(
            enabled=bool(self.plugin_setting("marquee_enabled", True)),
            speed_px_per_second=float(self.plugin_setting("marquee_speed_px_per_second", 32) or 32),
        )

    # -- UI ----------------------------------------------------------------

    def _action_icon(self, icon_name: str) -> Gtk.Widget:
        path = self.get_asset_path(f"{icon_name}.svg", subdirs=["icons"])
        if os.path.exists(path):
            return Gtk.Image.new_from_file(path)
        return Gtk.Image(icon_name="audio-x-generic-symbolic")

    def get_settings_area(self):
        from .ui.plugin_settings import SpotifySettingsArea

        self._settings_area = SpotifySettingsArea(self)
        return self._settings_area.build()

    def _refresh_settings_ui(self) -> None:
        if self._settings_area is not None:
            self._settings_area.refresh()

    def get_selector_icon(self) -> Gtk.Widget:
        return self._action_icon("playback_dial")

    # -- shutdown -----------------------------------------------------------

    def _on_quit(self, *args, **kwargs) -> None:
        log.info("Deck Essentials for Spotify: shutting down")
        try:
            self.spotify.shutdown()
        except Exception:  # noqa: BLE001 - quitting must not be blocked
            log.exception("Spotify: error during shutdown")

    def on_uninstall(self) -> None:
        self._on_quit()
        super().on_uninstall()
