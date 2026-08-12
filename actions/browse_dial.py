"""Spotify: My Playlists and My Liked Songs — the two browsing dials.

Both work the same way: rotate to move through a collection, push or tap to play
what is selected, hold a tap to reload. The selection lives in the action, not
in the manager, so two dials can sit on the same deck pointing at different
places in the same list.
"""

from __future__ import annotations

from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering.dial import render_browse_dial
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase

#: How long "REFRESHING" stays on screen after a long tap.
HINT_SECONDS = 1.5


class _BrowseDialBase(SpotifyActionBase):
    ICON = "playlist_dial"
    WANTS_MARQUEE = True

    EXTRA_DEFAULTS = {
        "wrap": True,
        "show_artwork": True,
        "marquee": True,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._index = 0
        self._hint: str | None = None
        self._hint_until = 0.0

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        self._wrap_row = SwitchRow(
            action_core=self,
            var_name="wrap",
            default_value=True,
            title="Wrap around",
            subtitle="Turning past the end returns to the start",
        )
        self._artwork_row = SwitchRow(
            action_core=self,
            var_name="show_artwork",
            default_value=True,
            title="Artwork",
            on_change=self._on_display_changed,
        )
        self._marquee_row = SwitchRow(
            action_core=self,
            var_name="marquee",
            default_value=True,
            title="Scroll long names",
            on_change=self._on_display_changed,
        )

    def _on_display_changed(self, _widget, _new_value, _old_value) -> None:
        self._last_signature = None
        self.render()

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_cw",
                ui_label="Next item",
                default_events=[Input.Dial.Events.TURN_CW],
                callback=lambda data=None: self._move(1),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_ccw",
                ui_label="Previous item",
                default_events=[Input.Dial.Events.TURN_CCW],
                callback=lambda data=None: self._move(-1),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_push",
                ui_label="Play selected",
                default_events=[Input.Dial.Events.SHORT_UP, Input.Key.Events.SHORT_UP],
                callback=lambda data=None: self._play_selected(),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_touch",
                ui_label="Play selected (touchscreen)",
                default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS],
                callback=lambda data=None: self._play_selected(),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_refresh",
                ui_label="Refresh the list",
                default_events=[Input.Dial.Events.LONG_TOUCH_PRESS, Input.Key.Events.HOLD_START],
                callback=lambda data=None: self._refresh(),
            )
        )

    def _move(self, delta: int) -> None:
        total = self.total()
        wrap = bool(self.setting("wrap", True))

        if total is None:
            self._index = max(0, self._index + delta)
        elif total <= 0:
            self._index = 0
        elif wrap:
            self._index = (self._index + delta) % total
        else:
            self._index = max(0, min(total - 1, self._index + delta))

        self.ensure_loaded(self._index)
        self._last_signature = None
        self.render()

    def _refresh(self) -> None:
        import time

        self._index = 0
        self.reload()
        self._hint = "REFRESHING"
        self._hint_until = time.monotonic() + HINT_SECONDS
        self._last_signature = None
        self.render()

    def _active_hint(self) -> str | None:
        import time

        if self._hint and time.monotonic() < self._hint_until:
            return self._hint
        self._hint = None
        return None

    def on_action_ready(self) -> None:
        self.ensure_loaded(self._index)

    # -- collection hooks -------------------------------------------------

    def total(self) -> int | None:
        raise NotImplementedError

    def ensure_loaded(self, index: int) -> None:
        raise NotImplementedError

    def reload(self) -> None:
        raise NotImplementedError

    def item(self, index: int):
        raise NotImplementedError

    def item_title(self, item) -> str:
        raise NotImplementedError

    def item_subtitle(self, item) -> str:
        return ""

    def item_artwork_url(self, item) -> str | None:
        return None

    def _play_selected(self) -> None:
        raise NotImplementedError

    def empty_detail(self) -> str:
        return "EMPTY"

    # -- rendering --------------------------------------------------------

    def state_signature(self):
        item = self.item(self._index)
        return None if self.marquee_enabled() else (
            self.blocking_status(),
            self._index,
            self.total(),
            self.item_title(item) if item else None,
            self._active_hint(),
        )

    def render_image(self):
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)

        total = self.total()
        item = self.item(self._index)

        if total == 0:
            return self.render_status(ActionStatus.UNAVAILABLE, detail=self.empty_detail())

        if item is None:
            # The page containing this index has not arrived yet.
            self.ensure_loaded(self._index)
            return self.render_status(ActionStatus.PENDING, detail="LOADING")

        artwork = None
        if bool(self.setting("show_artwork", True)):
            artwork = self.manager.artwork.get(self.item_artwork_url(item))

        title = self.item_title(item)
        position = f"{self._index + 1} / {total}" if total else f"{self._index + 1}"

        result = render_browse_dial(
            self.image_size(),
            title=title,
            subtitle=self.item_subtitle(item),
            position_text=position,
            artwork=artwork,
            hint=self._active_hint(),
            icon=self.ICON,
            title_offset=self.marquee_offset(),
        )

        self.update_marquee(result.title_overflow, title)
        return result.image


class PlaylistsDialAction(_BrowseDialBase):
    TITLE = "Playlists"
    ICON = "playlist_dial"
    TOPICS = frozenset({"playback", "auth", "playlists"})

    def _playlists(self):
        return self.manager.get_playlists()

    def total(self) -> int | None:
        playlists = self._playlists()
        return None if playlists is None else len(playlists)

    def ensure_loaded(self, index: int) -> None:
        # Requesting the list is what starts the background load.
        self._playlists()

    def reload(self) -> None:
        self.manager.refresh_playlists()

    def item(self, index: int):
        playlists = self._playlists()
        if not playlists:
            return None
        if 0 <= index < len(playlists):
            return playlists[index]
        return None

    def item_title(self, item) -> str:
        return item.name

    def item_subtitle(self, item) -> str:
        return f"{item.track_count} songs" if item.track_count is not None else ""

    def item_artwork_url(self, item) -> str | None:
        return item.artwork_url

    def empty_detail(self) -> str:
        return "NO\nPLAYLISTS"

    def _play_selected(self) -> None:
        item = self.item(self._index)
        if item is None:
            self.report_failure()
            return
        self.manager.play_context(item.uri, self.device_id)
        self.flash("PLAYING")


class LikedSongsDialAction(_BrowseDialBase):
    TITLE = "Liked"
    ICON = "liked_songs_dial"
    TOPICS = frozenset({"playback", "auth", "liked"})

    def total(self) -> int | None:
        return self.manager.get_liked_songs_total()

    def ensure_loaded(self, index: int) -> None:
        self.manager.ensure_liked_songs(index)

    def reload(self) -> None:
        self.manager.refresh_liked_songs()

    def item(self, index: int):
        return self.manager.get_liked_song(index)

    def item_title(self, item) -> str:
        return item.name

    def item_subtitle(self, item) -> str:
        return item.artist_text

    def item_artwork_url(self, item) -> str | None:
        return item.artwork_url

    def empty_detail(self) -> str:
        return "NO LIKED\nSONGS"

    def _play_selected(self) -> None:
        item = self.item(self._index)
        if item is None:
            self.report_failure()
            return
        self.manager.play_track(item.uri, self.device_id)
        self.flash("PLAYING")
