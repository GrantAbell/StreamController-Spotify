"""The browsing dials: My Playlists, Queue Picker and Song Picker.

All three work the same way: rotate to move through a list, push or tap to play
what is selected, hold for a second action. What differs is where the list comes
from — your playlists, the play queue, or one collection you pointed the dial at.
The selection lives in the action, not in the manager, so two dials can sit on
the same deck pointing at different places in the same list.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering.dial import render_browse_dial
from ..spotify.manager import QUEUE_LIMIT
from ..spotify.state import ActionStatus
from ..spotify.uri import browsable_context, parse_resource, playable_context
from .base import SpotifyActionBase

#: How long "REFRESHING" stays on screen after a long tap.
HINT_SECONDS = 1.5

ACTION_QUEUE = "queue"
ACTION_PLAY = "play"
ACTION_REFRESH = "refresh"
ACTION_NOTHING = "nothing"

#: What either gesture on a picker can be set to, in the order shown. The
#: stored values are shared, so a dial words "play" to suit what it browses.
PICKER_ACTIONS = (
    (ACTION_PLAY, "Play the selected song"),
    (ACTION_QUEUE, "Add to queue"),
    (ACTION_REFRESH, "Refresh the list"),
    (ACTION_NOTHING, "Do nothing"),
)

QUEUE_ACTIONS = (
    (ACTION_PLAY, "Jump to this song"),
    (ACTION_QUEUE, "Queue it again"),
    (ACTION_REFRESH, "Refresh the list"),
    (ACTION_NOTHING, "Do nothing"),
)


class _BrowseDialBase(SpotifyActionBase):
    ICON = "playlist_dial"
    WANTS_MARQUEE = True

    EXTRA_DEFAULTS = {
        "wrap": True,
        "show_artwork": True,
        "marquee": True,
    }

    #: Inputs that run the hold action. One assigner covers them all: two
    #: assigners sharing an event would both fire on the same press.
    HOLD_EVENTS = (Input.Dial.Events.LONG_TOUCH_PRESS, Input.Key.Events.HOLD_START)
    HOLD_LABEL = "Refresh the list"
    PRESS_LABEL = "Play selected"

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
                ui_label=self.PRESS_LABEL,
                default_events=[Input.Dial.Events.SHORT_UP, Input.Key.Events.SHORT_UP],
                callback=lambda data=None: self._on_press(),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_touch",
                ui_label=f"{self.PRESS_LABEL} (touchscreen)",
                default_events=[Input.Dial.Events.SHORT_TOUCH_PRESS],
                callback=lambda data=None: self._on_press(),
            )
        )
        self.add_event_assigner(
            EventAssigner(
                id="spotify_browse_refresh",
                ui_label=self.HOLD_LABEL,
                default_events=list(self.HOLD_EVENTS),
                callback=lambda data=None: self._on_hold(),
            )
        )

    def _on_press(self) -> None:
        """What a press or a tap does. Subclasses may offer a choice."""
        self._play_selected()

    def _on_hold(self) -> None:
        """What a hold or a long screen tap does. Subclasses may offer a choice."""
        self._refresh()

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


class _PickerDialBase(_BrowseDialBase):
    """A browse dial whose items are songs and whose hold is the user's choice.

    Both pickers browse a list of songs and act on one of them, so the hold
    menu, the queueing and the way a song is drawn live here.
    """

    TOPICS = frozenset({"playback", "auth", "browse"})
    EXTRA_DEFAULTS = {
        **_BrowseDialBase.EXTRA_DEFAULTS,
        "press_action": ACTION_PLAY,
        "hold_action": ACTION_QUEUE,
    }

    #: What both gestures can be set to, worded for what this dial browses.
    ACTION_CHOICES = PICKER_ACTIONS
    PRESS_DEFAULT = ACTION_PLAY

    #: The dial's own press-and-hold is included: acting on the song you are
    #: looking at is worth a physical gesture, not only a long screen tap.
    HOLD_EVENTS = (
        Input.Dial.Events.LONG_TOUCH_PRESS,
        Input.Dial.Events.HOLD_START,
        Input.Key.Events.HOLD_START,
    )
    HOLD_LABEL = "Press and hold"
    HOLD_DEFAULT = ACTION_QUEUE

    def build_action_ui(self) -> None:
        super().build_action_ui()
        self._press_row = ComboRow(
            action_core=self,
            var_name="press_action",
            default_value=self.PRESS_DEFAULT,
            items=[SimpleComboRowItem(value, label) for value, label in self.ACTION_CHOICES],
            title="Press",
            subtitle="A quick press of the dial, or a tap on the touchscreen",
        )
        self._hold_row = ComboRow(
            action_core=self,
            var_name="hold_action",
            default_value=self.HOLD_DEFAULT,
            items=[SimpleComboRowItem(value, label) for value, label in self.ACTION_CHOICES],
            title="Press and hold",
            subtitle="Holding the dial in, or holding a tap on the touchscreen",
        )

    def _on_press(self) -> None:
        self._run_action(self.combo_value("press_action", self.PRESS_DEFAULT))

    def _on_hold(self) -> None:
        self._run_action(self.combo_value("hold_action", self.HOLD_DEFAULT))

    def _run_action(self, action: str) -> None:
        if action == ACTION_REFRESH:
            self._refresh()
        elif action == ACTION_PLAY:
            self._play_selected()
        elif action == ACTION_QUEUE:
            self._queue_selected()

    def _queue_selected(self) -> None:
        item = self.item(self._index)
        if item is None or not item.uri:
            self.report_failure()
            return
        self.manager.add_to_queue(
            item.uri,
            self.device_id,
            on_result=lambda ok: self.flash("QUEUED") if ok else self.report_failure(),
        )

    # -- songs -------------------------------------------------------------

    def item_title(self, item) -> str:
        return item.name

    def item_subtitle(self, item) -> str:
        return item.artist_text

    def item_artwork_url(self, item) -> str | None:
        return item.artwork_url


class QueuePickerAction(_PickerDialBase):
    """Spotify: Queue Picker — what is playing and what comes after it.

    Spotify publishes only the next twenty songs, so that is the whole of what
    this dial can show; the Song Picker is the one that scrolls a collection
    without a ceiling. Playing something further down jumps to it the way the
    app does, dropping what was in between.
    """

    TITLE = "Queue"
    ICON = "queue_dial"

    ACTION_CHOICES = QUEUE_ACTIONS
    PRESS_LABEL = "Press action"

    # A press jumps to the song; the queue moves on its own, so re-reading it
    # is the useful hold. Both are only defaults — see the settings.
    PRESS_DEFAULT = ACTION_PLAY
    HOLD_DEFAULT = ACTION_REFRESH
    EXTRA_DEFAULTS = {
        **_PickerDialBase.EXTRA_DEFAULTS,
        "press_action": ACTION_PLAY,
        "hold_action": ACTION_REFRESH,
    }

    def get_extra_config_rows(self) -> list:
        group = Adw.PreferencesGroup(title="What this dial can and cannot do")
        group.add(
            Adw.ActionRow(
                title=f"Spotify shows only the next {QUEUE_LIMIT} songs",
                subtitle="That is everything its API will hand over, however long the queue really is. "
                "To scroll a whole album, playlist or your Liked Songs, use the Song Picker instead.",
            )
        )
        return [group]

    def _tracks(self):
        return self.manager.get_queue_tracks()

    def total(self) -> int | None:
        tracks = self._tracks()
        return None if tracks is None else len(tracks)

    def ensure_loaded(self, index: int) -> None:
        self.manager.ensure_queue()

    def reload(self) -> None:
        self.manager.refresh_queue()

    def item(self, index: int):
        tracks = self._tracks() or []
        return tracks[index] if 0 <= index < len(tracks) else None

    def empty_detail(self) -> str:
        return "NOTHING\nQUEUED"

    def _play_selected(self) -> None:
        if self.item(self._index) is None:
            self.report_failure()
            return
        if self._index == 0:
            # Already playing: nothing to skip to.
            self.flash("PLAYING")
            return

        self.manager.skip_to_queue_index(self._index, self.device_id)
        self._index = 0
        self._last_signature = None
        self.flash("PLAYING")


class SongPickerAction(_PickerDialBase):
    """Spotify: Song Picker — scroll through one album, playlist or Liked Songs.

    The collection is whatever the user pasted a share link to, browsed a page
    at a time so a library of thousands costs one request per fifty songs.
    """

    TITLE = "Songs"
    ICON = "song_picker_dial"

    EXTRA_DEFAULTS = {**_PickerDialBase.EXTRA_DEFAULTS, "spotify_resource": ""}

    def __init__(self, *args, **kwargs):
        self._context_status_row: Adw.ActionRow | None = None
        self._context_seen: str | None = None
        super().__init__(*args, **kwargs)

    def build_action_ui(self) -> None:
        super().build_action_ui()
        self._resource_row = EntryRow(
            action_core=self,
            var_name="spotify_resource",
            default_value="",
            title="Album, playlist or Liked Songs link",
            on_change=self._on_resource_changed,
        )

    # -- what is being browsed --------------------------------------------

    def context_uri(self) -> str | None:
        """What to scroll: the pasted link, or else whatever is playing.

        Following playback is the default because it is what a picker is for —
        the collection you are listening to, without being told which. A pasted
        link pins the dial to one collection and stops it following.
        """
        resource = parse_resource(self.setting("spotify_resource", ""))
        if resource is not None and browsable_context(resource.uri):
            return resource.uri
        if str(self.setting("spotify_resource", "") or "").strip():
            # Something was pasted, and it is not a collection: do not quietly
            # follow playback instead, or the dial looks like it accepted it.
            return None

        playing = self.manager.get_playback_state().context_uri
        return playing if browsable_context(playing) else None

    def _sync_context(self) -> None:
        """Start from the top whenever the collection underfoot changes."""
        current = self.context_uri()
        if current != self._context_seen:
            self._context_seen = current
            self._index = 0
            self._last_signature = None

    def get_extra_config_rows(self) -> list:
        group = Adw.PreferencesGroup(
            title="What to scroll through",
            description="Left empty, the dial follows whatever you are playing from and changes with it. "
            "Paste a share link to an album, a playlist or your Liked Songs to pin it to one collection "
            "instead; Use what is playing fills that in from what is playing now.",
        )

        self._context_status_row = Adw.ActionRow()
        button = Gtk.Button(label="Use what is playing", valign=Gtk.Align.CENTER)
        button.connect("clicked", self._on_capture_context)
        self._context_status_row.add_suffix(button)
        group.add(self._context_status_row)

        self._update_context_status()
        return [group]

    def _on_resource_changed(self, _widget, _new_value, _old_value) -> None:
        self._index = 0
        self._update_context_status()
        self.ensure_loaded(0)
        self._last_signature = None
        self.render()

    def _on_capture_context(self, _button) -> None:
        context_uri = self.manager.get_playback_state().context_uri
        if not context_uri:
            self._show_context_status(
                "Nothing is playing from a collection",
                "Start an album, a playlist or your Liked Songs in Spotify, then press this again.",
            )
            return

        self._resource_row.set_value(context_uri)
        self._resource_row.set_ui_value(context_uri)
        self._on_resource_changed(None, None, None)

    def _update_context_status(self) -> None:
        text = str(self.setting("spotify_resource", "") or "").strip()
        if not text:
            playing = self.context_uri()
            if playing is None:
                self._show_context_status("Following playback", "Nothing is playing from a collection right now.")
                return
            self.manager.ensure_context_details(playing)
            name = self.manager.get_context_name(playing)
            self._show_context_status(f"Following playback — {name or 'resolving…'}", playing)
            return

        uri = self.context_uri()
        if uri is None:
            self._show_context_status("Not something with a song list", text)
            return

        self.manager.ensure_context_details(uri)
        name = self.manager.get_context_name(uri)
        self._show_context_status(name or "Resolving…", uri)

    def _show_context_status(self, title: str, subtitle: str) -> None:
        if self._context_status_row is not None:
            self._context_status_row.set_title(title)
            self._context_status_row.set_subtitle(subtitle)

    # -- the collection ----------------------------------------------------

    def state_signature(self):
        signature = super().state_signature()
        return None if signature is None else (self.context_uri(), *signature)

    def total(self) -> int | None:
        return self.manager.get_context_track_total(self.context_uri())

    def ensure_loaded(self, index: int) -> None:
        self._sync_context()
        self.manager.ensure_context_tracks(self.context_uri(), index)

    def reload(self) -> None:
        self.manager.refresh_context_tracks(self.context_uri())

    def item(self, index: int):
        return self.manager.get_context_track(self.context_uri(), index)

    def item_artwork_url(self, item) -> str | None:
        # An album's own tracks carry no cover of their own, so the collection
        # being browsed supplies it.
        return item.artwork_url or self.manager.get_context_artwork_url(self.context_uri())

    def empty_detail(self) -> str:
        return "NO SONGS" if self.context_uri() else "NO LINK"

    def render_image(self):
        self._sync_context()
        if self.context_uri() is None and not self.blocking_status():
            # Nothing pasted and nothing playing from a collection.
            return self.render_status(ActionStatus.UNKNOWN, detail="NOTHING\nTO SCROLL")
        return super().render_image()

    def _play_selected(self) -> None:
        item = self.item(self._index)
        context_uri = self.context_uri()
        if item is None or context_uri is None:
            self.report_failure()
            return

        if playable_context(context_uri):
            # Spotify's own context, so it plays on past the end of the run.
            self.manager.play_context_at(context_uri, item.uri, self.device_id)
        else:
            # Liked Songs, which Spotify will not start from a context URI.
            self.manager.play_run_from(context_uri, self._index, self.device_id)

        self.flash("PLAYING")
