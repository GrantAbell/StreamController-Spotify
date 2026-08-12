"""The single Spotify owner for the whole plugin.

Exactly one of these exists. Actions read cached state from it and hand it
commands; they never touch HTTP, never poll, and never own a thread. Everything
that talks to Spotify — one poll loop and one command worker — lives here, so
the API traffic is a function of how much Spotify state exists, not of how many
keys the user happened to place on a page.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterable

import requests

from .api import MAX_PAGE_SIZE, SpotifyApiClient
from .artwork import ArtworkCache
from .auth import DEFAULT_CALLBACK_PORT, SpotifyAuthManager, TokenStore
from .cache import PagedCache, TtlCache
from .commands import KEY_SEEK, KEY_VOLUME, CommandQueue
from .errors import (
    SpotifyApiError,
    SpotifyAuthError,
    SpotifyNetworkError,
    SpotifyNoDeviceError,
    SpotifyPluginError,
    SpotifyRateLimitError,
    SpotifyRestrictedError,
    SpotifyShutdownError,
)
from .log import debug, log
from .models import (
    EMPTY_PLAYBACK,
    PlaybackState,
    SpotifyDevice,
    SpotifyPlaylist,
    SpotifyTrack,
    UserProfile,
    parse_devices,
    parse_playback,
    parse_playlists,
    parse_profile,
    parse_saved_tracks,
)
from .state import (
    FALLBACK_UNMUTE_VOLUME,
    ActionStatus,
    LikeState,
    clamp_position,
    clamp_volume,
    interpolated_progress_ms,
    is_music_track,
    playback_status,
    seek_target_ms,
)
from .uri import parse_resource

TOPIC_PLAYBACK = "playback"
TOPIC_AUTH = "auth"
TOPIC_DEVICES = "devices"
TOPIC_PLAYLISTS = "playlists"
TOPIC_LIKED = "liked"
TOPIC_LIBRARY = "library"

DEFAULT_TOPICS = frozenset({TOPIC_PLAYBACK, TOPIC_AUTH, TOPIC_LIBRARY})

#: How long an optimistic value overrides what the last poll reported. Long
#: enough to cover a round trip, short enough that a command Spotify quietly
#: ignored corrects itself within a couple of polls.
OPTIMISTIC_TTL = 3.0

#: Idle polling when nothing is playing, so an untouched deck is not making a
#: request every second all day.
IDLE_POLL_MULTIPLIER = 4


class SpotifyManager:
    def __init__(
        self,
        token_path: str,
        settings_provider: Callable[[], dict],
        marquee=None,
        session: requests.Session | None = None,
        api=None,
        open_url: Callable[[str], None] | None = None,
        auto_start: bool = True,
    ):
        self._settings_provider = settings_provider
        self.marquee = marquee

        # One long-lived session for API, token and artwork traffic.
        self._session = session or requests.Session()

        self.auth = SpotifyAuthManager(
            token_store=TokenStore(token_path),
            session=self._session,
            client_id_provider=lambda: self.setting("spotify_client_id", ""),
            port_provider=lambda: int(self.setting("callback_port", DEFAULT_CALLBACK_PORT) or DEFAULT_CALLBACK_PORT),
            open_url=open_url,
            on_change=self._on_auth_changed,
        )
        self.api = api if api is not None else SpotifyApiClient(self.auth, session=self._session)

        self.artwork = ArtworkCache(session=self._session, on_loaded=self._on_artwork_loaded)

        self._lock = threading.RLock()

        self._playback: PlaybackState = EMPTY_PLAYBACK
        self._devices: list[SpotifyDevice] = []
        self._profile: UserProfile | None = None
        self._playlists: list[SpotifyPlaylist] | None = None
        self._playlists_loading = False
        self._liked: PagedCache[SpotifyTrack] = PagedCache(page_size=MAX_PAGE_SIZE)

        self._like_state: TtlCache[str, bool] = TtlCache(ttl_seconds=900.0)
        self._like_pending: set[str] = set()
        self._context_names: TtlCache[str, str] = TtlCache(ttl_seconds=1800.0)
        self._context_requested: set[str] = set()

        self._optimistic: dict[str, tuple[object, float]] = {}
        self._last_nonzero_volume: dict[str, int] = {}

        self._rate_limited_until: float = 0.0
        self._last_error: Exception | None = None

        self._listeners: dict[str, set[Callable[[], None]]] = {
            topic: set()
            for topic in (TOPIC_PLAYBACK, TOPIC_AUTH, TOPIC_DEVICES, TOPIC_PLAYLISTS, TOPIC_LIKED, TOPIC_LIBRARY)
        }

        self._queue = CommandQueue()
        #: Pause between a player command and the poll that reads back its
        #: effect. Spotify's own state lags its commands slightly, so polling
        #: instantly would just re-read the old value. Tests set this to zero.
        self.command_settle_seconds = 0.25
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []

        if auto_start:
            self.start()

    # -- settings ---------------------------------------------------------

    def setting(self, key: str, fallback=None):
        try:
            return (self._settings_provider() or {}).get(key, fallback)
        except Exception:  # noqa: BLE001 - settings must never break a command
            return fallback

    @property
    def poll_interval(self) -> float:
        try:
            return max(0.5, float(self.setting("playback_poll_interval_ms", 1000)) / 1000.0)
        except (TypeError, ValueError):
            return 1.0

    @property
    def device_refresh_interval(self) -> float:
        try:
            return max(5.0, float(self.setting("device_refresh_interval_ms", 15000)) / 1000.0)
        except (TypeError, ValueError):
            return 15.0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._poll_loop, name="spotify-poll", daemon=True),
            threading.Thread(target=self._command_loop, name="spotify-commands", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def shutdown(self) -> None:
        """Stop everything, with bounded waits so quitting never hangs."""
        self._stopping.set()
        self._wake.set()
        self._queue.close()
        self.auth.shutdown()

        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads = []

        self.artwork.shutdown()
        try:
            self.api.close()
        except AttributeError:
            pass
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass

        with self._lock:
            for listeners in self._listeners.values():
                listeners.clear()

        if self.marquee is not None:
            self.marquee.stop()

        log.info("Spotify: manager stopped")

    # -- listeners --------------------------------------------------------

    def add_listener(self, callback: Callable[[], None], topics: Iterable[str] = DEFAULT_TOPICS) -> None:
        with self._lock:
            for topic in topics:
                self._listeners.setdefault(topic, set()).add(callback)
        # A newly placed action should not wait up to a full poll for its first
        # real state.
        self._wake.set()

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            for listeners in self._listeners.values():
                listeners.discard(callback)

    def _notify(self, *topics: str) -> None:
        with self._lock:
            callbacks: set[Callable[[], None]] = set()
            for topic in topics:
                callbacks |= self._listeners.get(topic, set())

        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - one bad action must not stop the rest
                log.exception("Spotify: listener raised while handling a state change")

    def _has_listeners(self) -> bool:
        """Whether anything on screen actually needs playback state.

        Auth listeners are excluded deliberately: the settings window watches
        that topic permanently, and it must not be the reason Spotify gets
        polled once a second with no Spotify actions on the deck.
        """
        with self._lock:
            return any(
                listeners for topic, listeners in self._listeners.items() if topic != TOPIC_AUTH
            )

    def _on_auth_changed(self) -> None:
        if self.auth.is_authenticated:
            self._wake.set()
            self.submit(self._refresh_profile, coalesce_key="profile")
        else:
            with self._lock:
                self._playback = EMPTY_PLAYBACK
                self._devices = []
                self._profile = None
                self._playlists = None
                self._optimistic.clear()
                self._like_state.clear()
                self._liked.clear()
        self._notify(TOPIC_AUTH, TOPIC_PLAYBACK, TOPIC_DEVICES, TOPIC_PLAYLISTS, TOPIC_LIKED, TOPIC_LIBRARY)

    def _on_artwork_loaded(self, _url: str) -> None:
        # Includes auth, because the User Information action draws the profile
        # picture through the same cache.
        self._notify(TOPIC_PLAYBACK, TOPIC_PLAYLISTS, TOPIC_LIKED, TOPIC_AUTH)

    # -- status -----------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return self.auth.is_authenticated

    @property
    def profile(self) -> UserProfile | None:
        with self._lock:
            return self._profile

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    @property
    def rate_limited_until(self) -> float:
        with self._lock:
            return self._rate_limited_until

    @property
    def is_rate_limited(self) -> bool:
        return self.rate_limited_until > time.monotonic()

    def get_status(self) -> ActionStatus:
        with self._lock:
            return playback_status(
                authenticated=self.auth.is_authenticated,
                rate_limited_until=self._rate_limited_until,
                last_error=self._last_error,
                state=self._playback,
                # An idle Spotify that is merely sitting there with nothing
                # playing is not "no device": the controls work, and pressing
                # play is exactly how you wake it up.
                has_available_device=any(device.id and not device.is_restricted for device in self._devices),
            )

    # -- playback state ---------------------------------------------------

    def get_playback_state(self) -> PlaybackState:
        """Cached playback with any in-flight optimistic values applied.

        Optimistic values are what make a button feel instant: the deck shows
        the state the user just asked for while the request is still in the air.
        """
        with self._lock:
            state = self._playback
            overrides = self._live_optimistic()

        if not overrides:
            return state

        replacements = {}
        if "is_playing" in overrides:
            replacements["is_playing"] = bool(overrides["is_playing"])
        if "shuffle" in overrides:
            replacements["shuffle"] = bool(overrides["shuffle"])
        if "repeat_mode" in overrides:
            replacements["repeat_mode"] = overrides["repeat_mode"]
        if "volume_percent" in overrides:
            volume = clamp_volume(overrides["volume_percent"])
            replacements["volume_percent"] = volume
            if state.device is not None:
                replacements["device"] = SpotifyDevice(
                    id=state.device.id,
                    name=state.device.name,
                    device_type=state.device.device_type,
                    is_active=state.device.is_active,
                    is_restricted=state.device.is_restricted,
                    volume_percent=volume,
                    supports_volume=state.device.supports_volume,
                )
        if "progress_ms" in overrides:
            replacements["progress_ms"] = int(overrides["progress_ms"])
            replacements["last_updated_monotonic"] = time.monotonic()

        if not replacements:
            return state

        from dataclasses import replace

        return replace(state, **replacements)

    def get_progress_ms(self) -> int | None:
        return interpolated_progress_ms(self.get_playback_state())

    def get_devices(self) -> list[SpotifyDevice]:
        with self._lock:
            return list(self._devices)

    def get_device_by_id(self, device_id: str | None) -> SpotifyDevice | None:
        if not device_id:
            return None
        for device in self.get_devices():
            if device.id == device_id:
                return device
        return None

    def preferred_device_id(self) -> str | None:
        """A device to command when Spotify reports no active one.

        Opening Spotify without pressing play leaves the desktop client listed
        as an available device while `/me/player` returns nothing at all. Without
        this, every playback action would sit at "No Device" until the user
        started something by hand — and pressing play would 404, because Spotify
        has nowhere to send it.
        """
        devices = self.get_devices()

        active = next((device for device in devices if device.is_active and device.id), None)
        if active is not None:
            return active.id

        usable = [device for device in devices if device.id and not device.is_restricted]
        if not usable:
            return None
        return usable[0].id

    def target_device(self) -> SpotifyDevice | None:
        """The device the next command would reach, playing or idle."""
        state = self.get_playback_state()
        if state.device is not None:
            return state.device
        return self.get_device_by_id(self.preferred_device_id())

    def supports_volume(self) -> bool:
        device = self.target_device()
        return bool(device and device.supports_volume and not device.is_restricted)

    def _send_to(self, device_id: str | None) -> str | None:
        """The device ID to put on a request, decided as it is sent.

        A fixed target is honoured as given. Otherwise Spotify is left to use
        its own active device — unless there isn't one, in which case an idle
        device is named explicitly so the command has somewhere to land.
        """
        if device_id:
            return device_id

        state = self.get_playback_state()
        if state.has_playback and state.device is not None and state.device.id:
            return None

        return self.preferred_device_id()

    def resolve_device_id(self, mode: str, device_id: str | None) -> tuple[str | None, bool]:
        """Which device a command targets, and whether the target is missing.

        A fixed target that has disappeared is reported as missing rather than
        being silently redirected to whatever happens to be playing.
        """
        if mode != "specific" or not device_id:
            return (None, False)

        device = self.get_device_by_id(device_id)
        if device is None:
            return (device_id, True)
        return (device_id, False)

    # -- optimistic state -------------------------------------------------

    def _set_optimistic(self, field: str, value) -> None:
        with self._lock:
            self._optimistic[field] = (value, time.monotonic() + OPTIMISTIC_TTL)

    def _get_optimistic(self, field: str, fallback=None):
        with self._lock:
            entry = self._optimistic.get(field)
            if entry is None or entry[1] <= time.monotonic():
                return fallback
            return entry[0]

    def _live_optimistic(self) -> dict:
        now = time.monotonic()
        expired = [field for field, (_, expires) in self._optimistic.items() if expires <= now]
        for field in expired:
            del self._optimistic[field]
        return {field: value for field, (value, _) in self._optimistic.items()}

    def _clear_optimistic(self, *fields: str) -> None:
        with self._lock:
            for field in fields:
                self._optimistic.pop(field, None)

    # -- command plumbing -------------------------------------------------

    def submit(self, work: Callable[[], None], coalesce_key: str | None = None) -> None:
        if self._stopping.is_set():
            return
        self._queue.submit(work, coalesce_key)

    def _command_loop(self) -> None:
        while not self._stopping.is_set():
            work = self._queue.take(timeout=0.2)
            if work is None:
                continue
            if not self.run_command(work):
                return

    def run_command(self, work: Callable[[], None]) -> bool:
        """Run one queued command, absorbing anything it raises.

        Returns False only when the plugin is shutting down. Separate from the
        loop so the tests can drive commands through exactly the same error
        handling the worker uses.
        """
        try:
            work()
        except SpotifyShutdownError:
            return False
        except SpotifyPluginError as error:
            self._record_error(error)
        except Exception as error:  # noqa: BLE001 - the worker must survive anything
            log.exception(f"Spotify: command failed ({error.__class__.__name__})")
            self._record_error(SpotifyApiError(0, "Unexpected Spotify error"))
        return True

    def _record_error(self, error: Exception) -> None:
        notify_topics = [TOPIC_PLAYBACK, TOPIC_AUTH]

        if isinstance(error, SpotifyRateLimitError):
            with self._lock:
                self._rate_limited_until = time.monotonic() + error.retry_after
                self._last_error = error
            log.warning(f"Spotify rate limited for {error.retry_after:.0f} seconds")
        elif isinstance(error, SpotifyAuthError):
            with self._lock:
                self._last_error = error
            log.warning("Spotify authentication is no longer valid")
        elif isinstance(error, SpotifyNoDeviceError):
            with self._lock:
                self._last_error = None
                # No device is a normal condition, not a failure; the state
                # itself already says there is nothing to control.
                self._playback = PlaybackState(last_updated_monotonic=time.monotonic(), has_playback=False)
        elif isinstance(error, SpotifyRestrictedError):
            with self._lock:
                self._last_error = error
            log.info(f"Spotify refused a command: {error}")
        elif isinstance(error, SpotifyNetworkError):
            with self._lock:
                self._last_error = error
            debug(f"Spotify unreachable: {error}")
        else:
            with self._lock:
                self._last_error = error
            log.warning(f"Spotify API error: {error}")

        self._notify(*notify_topics)

    def _clear_error(self) -> None:
        with self._lock:
            had_error = self._last_error is not None
            self._last_error = None
            self._rate_limited_until = 0.0
        if had_error:
            self._notify(TOPIC_PLAYBACK, TOPIC_AUTH)

    # -- polling ----------------------------------------------------------

    def _poll_loop(self) -> None:
        next_device_refresh = 0.0

        while not self._stopping.is_set():
            interval = self.poll_interval

            if not self.auth.is_authenticated or not self._has_listeners():
                # Nothing to show and nobody to show it to.
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue

            if self.is_rate_limited:
                remaining = max(0.5, self.rate_limited_until - time.monotonic())
                self._wake.wait(timeout=min(remaining, 5.0))
                self._wake.clear()
                continue

            try:
                self._poll_playback()

                now = time.monotonic()
                if now >= next_device_refresh:
                    self._refresh_devices()
                    # While nothing is playing, the device list is the only
                    # thing that can tell an action Spotify has woken up, so it
                    # is checked far more eagerly than during playback.
                    with self._lock:
                        idle_now = not self._playback.has_playback
                    next_device_refresh = now + (
                        max(2.0, self.poll_interval * 2) if idle_now else self.device_refresh_interval
                    )

            except SpotifyShutdownError:
                return
            except SpotifyPluginError as error:
                self._record_error(error)
            except Exception as error:  # noqa: BLE001
                log.exception(f"Spotify: poll failed ({error.__class__.__name__})")

            with self._lock:
                idle = not self._playback.is_playing
            wait_for = interval * (IDLE_POLL_MULTIPLIER if idle else 1)

            self._wake.wait(timeout=wait_for)
            self._wake.clear()

    def _poll_playback(self) -> None:
        payload = self.api.get_playback_state()
        state = parse_playback(payload)

        with self._lock:
            previous = self._playback
            self._playback = state
            self._reconcile_optimistic(state)
            if state.device and state.device.id and state.volume_percent:
                self._last_nonzero_volume[state.device.id] = state.volume_percent
            self._last_error = None

        track_changed = _track_uri(previous) != _track_uri(state)

        if track_changed:
            self._on_track_changed(state)

        if state.track and state.track.artwork_url:
            self.artwork.prefetch(state.track.artwork_url)

        if state.context_uri:
            self._ensure_context_name(state.context_uri)

        if _has_visible_change(previous, state):
            self._notify(TOPIC_PLAYBACK)

    def _reconcile_optimistic(self, state: PlaybackState) -> None:
        """Drop optimistic values Spotify has now confirmed.

        Held under the manager lock; called only from the poll thread.
        """
        now = time.monotonic()
        for field, actual in (
            ("is_playing", state.is_playing),
            ("shuffle", state.shuffle),
            ("repeat_mode", state.repeat_mode),
            ("volume_percent", state.volume_percent),
        ):
            entry = self._optimistic.get(field)
            if entry is None:
                continue
            desired, expires = entry
            if desired == actual or expires <= now:
                del self._optimistic[field]

        entry = self._optimistic.get("progress_ms")
        if entry is not None:
            desired, expires = entry
            if expires <= now:
                del self._optimistic["progress_ms"]
            elif state.progress_ms is not None and abs(state.progress_ms - int(desired)) < 1500:
                del self._optimistic["progress_ms"]

    def _on_track_changed(self, state: PlaybackState) -> None:
        track = state.track
        if self.marquee is not None:
            self.marquee.reset_all()

        if track is None or not is_music_track(state):
            self._notify(TOPIC_LIBRARY)
            return

        # Liked state is looked up per track, not per second.
        self.refresh_like_state(track.uri)

    def refresh_now(self) -> None:
        """Ask the poll loop to run immediately (after a command, or on ready)."""
        self._wake.set()

    def _refresh_devices(self) -> None:
        devices = parse_devices(self.api.get_devices())
        with self._lock:
            changed = _device_signature(self._devices) != _device_signature(devices)
            self._devices = devices
            for device in devices:
                if device.id and device.volume_percent:
                    self._last_nonzero_volume.setdefault(device.id, device.volume_percent)

        if changed:
            active = next((device for device in devices if device.is_active), None)
            log.info(f"Playback device changed: {active.name if active else 'none'}")
            self._notify(TOPIC_DEVICES, TOPIC_PLAYBACK)

    def refresh_devices(self) -> None:
        self.submit(self._refresh_devices, coalesce_key="devices")

    def _refresh_profile(self) -> None:
        profile = parse_profile(self.api.get_profile())
        with self._lock:
            self._profile = profile
        if profile is not None and profile.is_premium is False:
            log.warning("Spotify: this account is not Premium; playback control will be refused by Spotify")
        self._notify(TOPIC_AUTH)

    # -- transport commands -----------------------------------------------

    def play(self, device_id: str | None = None) -> None:
        self._set_optimistic("is_playing", True)
        self._notify(TOPIC_PLAYBACK)
        self.submit(lambda: self._run_playback(lambda: self.api.play(device_id=self._send_to(device_id))))

    def pause(self, device_id: str | None = None) -> None:
        self._set_optimistic("is_playing", False)
        self._notify(TOPIC_PLAYBACK)
        self.submit(lambda: self._run_playback(lambda: self.api.pause(device_id=self._send_to(device_id))))

    def toggle_playback(self, device_id: str | None = None) -> None:
        if self.get_playback_state().is_playing:
            self.pause(device_id)
        else:
            self.play(device_id)

    def next_track(self, device_id: str | None = None) -> None:
        self.submit(lambda: self._run_playback(lambda: self.api.next_track(device_id=self._send_to(device_id))))

    def previous_track(self, device_id: str | None = None) -> None:
        self.submit(lambda: self._run_playback(lambda: self.api.previous_track(device_id=self._send_to(device_id))))

    def seek_absolute(self, position_ms: int, device_id: str | None = None) -> None:
        state = self.get_playback_state()
        target = clamp_position(position_ms, state.duration_ms)
        self._set_optimistic("progress_ms", target)
        self._notify(TOPIC_PLAYBACK)
        self.submit(
            lambda: self._run_playback(lambda: self.api.seek(self._desired_seek(target), device_id=self._send_to(device_id))),
            coalesce_key=KEY_SEEK,
        )

    def seek_relative(self, delta_ms: int, device_id: str | None = None) -> None:
        state = self.get_playback_state()
        self.seek_absolute(seek_target_ms(state, delta_ms), device_id=device_id)

    def _desired_seek(self, fallback: int) -> int:
        """The newest requested position, so a coalesced seek lands correctly."""
        return int(self._get_optimistic("progress_ms", fallback))

    def set_shuffle(self, enabled: bool, device_id: str | None = None) -> None:
        self._set_optimistic("shuffle", bool(enabled))
        self._notify(TOPIC_PLAYBACK)
        self.submit(lambda: self._run_playback(lambda: self.api.set_shuffle(bool(enabled), device_id=self._send_to(device_id))))

    def toggle_shuffle(self, device_id: str | None = None) -> None:
        current = self.get_playback_state().shuffle
        self.set_shuffle(not bool(current), device_id=device_id)

    def set_repeat(self, mode: str, device_id: str | None = None) -> None:
        self._set_optimistic("repeat_mode", mode)
        self._notify(TOPIC_PLAYBACK)
        self.submit(lambda: self._run_playback(lambda: self.api.set_repeat(mode, device_id=self._send_to(device_id))))

    # -- volume -----------------------------------------------------------

    def get_volume(self) -> int | None:
        """The level of whichever device a command would reach.

        Falls back to the idle device's reported level, so the volume keys show
        something real before anything is playing.
        """
        state = self.get_playback_state()
        if state.device is not None:
            return state.volume_percent

        optimistic = self._get_optimistic("volume_percent")
        if optimistic is not None:
            return clamp_volume(optimistic)

        device = self.target_device()
        return device.volume_percent if device else None

    def set_volume(self, percent: int, device_id: str | None = None) -> None:
        target = clamp_volume(percent)
        state = self.get_playback_state()
        active_id = device_id or (state.device.id if state.device else self.preferred_device_id())

        with self._lock:
            if target > 0 and active_id:
                self._last_nonzero_volume[active_id] = target

        self._set_optimistic("volume_percent", target)
        self._notify(TOPIC_PLAYBACK)

        # Coalesced: only the newest requested level is worth sending, but the
        # newest one always is.
        self.submit(
            lambda: self._run_playback(
                lambda: self.api.set_volume(
                    clamp_volume(self._get_optimistic("volume_percent", target)),
                    device_id=self._send_to(device_id),
                )
            ),
            coalesce_key=KEY_VOLUME,
        )

    def adjust_volume(self, delta: int, device_id: str | None = None) -> None:
        current = self._get_optimistic("volume_percent", self.get_volume())
        if current is None:
            current = FALLBACK_UNMUTE_VOLUME
        self.set_volume(clamp_volume(int(current) + int(delta)), device_id=device_id)

    def toggle_mute(self, device_id: str | None = None) -> None:
        state = self.get_playback_state()
        active_id = device_id or (state.device.id if state.device else self.preferred_device_id())
        current = self._get_optimistic("volume_percent", self.get_volume())

        if current is None:
            return

        if int(current) > 0:
            self.set_volume(0, device_id=device_id)
            return

        with self._lock:
            restore = self._last_nonzero_volume.get(active_id or "", FALLBACK_UNMUTE_VOLUME)
        self.set_volume(restore, device_id=device_id)

    def is_muted(self) -> bool:
        volume = self.get_volume()
        return volume is not None and volume == 0

    def begin_hold_mute(self, device_id: str | None = None) -> int | None:
        """Mute now and report the level to restore when the hold ends."""
        current = self._get_optimistic("volume_percent", self.get_volume())
        if current is None:
            return None
        self.set_volume(0, device_id=device_id)
        return int(current)

    def end_hold_mute(self, restore_to: int | None, device_id: str | None = None, device_at_press: str | None = None) -> None:
        """Restore the pre-hold level, unless the device changed under us."""
        if restore_to is None:
            return

        state = self.get_playback_state()
        current_device = state.device.id if state.device else None
        if device_at_press is not None and current_device != device_at_press:
            log.info("Spotify: device changed during hold-mute; leaving the volume alone")
            return

        self.set_volume(restore_to, device_id=device_id)

    # -- library ----------------------------------------------------------

    def get_like_state(self, uri: str | None = None) -> LikeState:
        state = self.get_playback_state()
        uri = uri or _track_uri(state)
        if not uri or not is_music_track(state):
            return LikeState.UNKNOWN

        with self._lock:
            if uri in self._like_pending:
                return LikeState.BUSY

        liked = self._like_state.get(uri)
        if liked is None:
            return LikeState.UNKNOWN
        return LikeState.LIKED if liked else LikeState.NOT_LIKED

    def get_current_like_state(self) -> LikeState:
        return self.get_like_state()

    def refresh_like_state(self, uri: str | None = None) -> None:
        uri = uri or _track_uri(self.get_playback_state())
        if not uri:
            return

        def work():
            results = self.api.library_contains([uri])
            self._like_state.put(uri, bool(results[0]) if results else False)
            self._notify(TOPIC_LIBRARY, TOPIC_PLAYBACK)

        self.submit(work, coalesce_key=f"like-check:{uri}")

    def toggle_like(self, on_result: Callable[[bool], None] | None = None) -> None:
        state = self.get_playback_state()
        uri = _track_uri(state)
        if not uri or not is_music_track(state):
            log.info("Spotify: the current item cannot be saved to your library")
            return

        current = self._like_state.get(uri)
        target = not bool(current)

        with self._lock:
            self._like_pending.add(uri)
        self._notify(TOPIC_LIBRARY, TOPIC_PLAYBACK)

        def work():
            try:
                if target:
                    self.api.library_save([uri])
                else:
                    self.api.library_remove([uri])
                self._like_state.put(uri, target)
                if on_result is not None:
                    on_result(target)
            finally:
                with self._lock:
                    self._like_pending.discard(uri)
                self._notify(TOPIC_LIBRARY, TOPIC_PLAYBACK)

        self.submit(work)

    def add_current_to_playlist(self, playlist_id: str, on_result: Callable[[bool], None] | None = None) -> None:
        state = self.get_playback_state()
        uri = _track_uri(state)
        if not uri or not playlist_id:
            if on_result is not None:
                on_result(False)
            return

        def work():
            ok = False
            try:
                # Deliberately not de-duplicated: matching the reference
                # behaviour, each press adds the current item again.
                self.api.add_to_playlist(playlist_id, [uri])
                ok = True
            finally:
                if on_result is not None:
                    on_result(ok)

        self.submit(work)

    # -- playlists --------------------------------------------------------

    def get_playlists(self) -> list[SpotifyPlaylist] | None:
        """The cached playlists, or None while the first page is still coming."""
        with self._lock:
            if self._playlists is None and not self._playlists_loading and self.auth.is_authenticated:
                self._playlists_loading = True
                self.submit(self._load_playlists, coalesce_key="playlists")
            return list(self._playlists) if self._playlists is not None else None

    def refresh_playlists(self) -> None:
        with self._lock:
            self._playlists = None
            self._playlists_loading = True
        self._notify(TOPIC_PLAYLISTS)
        self.submit(self._load_playlists, coalesce_key="playlists")

    def _load_playlists(self) -> None:
        try:
            collected: list[SpotifyPlaylist] = []
            offset = 0
            total = None

            while not self._stopping.is_set():
                payload = self.api.get_playlists(limit=MAX_PAGE_SIZE, offset=offset)
                page = parse_playlists(payload)
                collected.extend(page)

                if total is None:
                    total = payload.get("total")

                # The first page is published immediately so the dial is usable
                # while the rest of a large collection is still downloading.
                with self._lock:
                    self._playlists = list(collected)
                self._notify(TOPIC_PLAYLISTS)

                if not page or not payload.get("next"):
                    break
                offset += len(page)
        finally:
            with self._lock:
                self._playlists_loading = False
            self._notify(TOPIC_PLAYLISTS)

    # -- liked songs ------------------------------------------------------

    @property
    def liked_songs(self) -> PagedCache[SpotifyTrack]:
        return self._liked

    def get_liked_song(self, index: int) -> SpotifyTrack | None:
        return self._liked.get(index)

    def get_liked_songs_total(self) -> int | None:
        return self._liked.total

    def ensure_liked_songs(self, index: int = 0) -> None:
        """Make sure the page containing `index` (and the next one) is loading."""
        if not self.auth.is_authenticated:
            return

        for offset in self._liked.missing_offsets(index):
            self._liked.mark_requested(offset)
            self.submit(lambda captured=offset: self._load_liked_page(captured), coalesce_key=f"liked:{offset}")

    def _load_liked_page(self, offset: int) -> None:
        try:
            payload = self.api.get_saved_tracks(limit=MAX_PAGE_SIZE, offset=offset)
            tracks = parse_saved_tracks(payload)
            self._liked.apply_page(offset, tracks, payload.get("total"))
            self._notify(TOPIC_LIKED)
        except SpotifyPluginError:
            # Allow a retry on the next navigation rather than leaving a
            # permanent hole in the collection.
            self._liked.unmark_requested(offset)
            raise

    def refresh_liked_songs(self) -> None:
        self._liked.clear()
        self._notify(TOPIC_LIKED)
        self.ensure_liked_songs(0)

    # -- contexts ---------------------------------------------------------

    def get_context_name(self, uri: str | None) -> str | None:
        if not uri:
            return None
        return self._context_names.get(uri)

    def _ensure_context_name(self, uri: str) -> None:
        if self._context_names.get(uri) is not None:
            return
        with self._lock:
            if uri in self._context_requested:
                return
            self._context_requested.add(uri)

        def work():
            try:
                payload = self.api.get_context(uri)
                name = (payload or {}).get("name")
                if name:
                    self._context_names.put(uri, name)
                    self._notify(TOPIC_PLAYBACK)
            finally:
                with self._lock:
                    self._context_requested.discard(uri)

        self.submit(work, coalesce_key=f"context:{uri}")

    # -- playing things ---------------------------------------------------

    def play_context(self, uri: str, device_id: str | None = None) -> None:
        resource = parse_resource(uri)
        if resource is None:
            log.warning("Spotify: not a usable Spotify link")
            return

        if resource.is_item:
            self.play_track(resource.uri, device_id=device_id)
            return

        self.submit(lambda: self._run_playback(lambda: self.api.play(device_id=self._send_to(device_id), context_uri=resource.uri)))

    def play_track(self, uri: str, device_id: str | None = None) -> None:
        self._set_optimistic("is_playing", True)
        self._notify(TOPIC_PLAYBACK)
        self.submit(lambda: self._run_playback(lambda: self.api.play(device_id=self._send_to(device_id), uris=[uri])))

    def transfer_playback(self, device_id: str, start_playing: bool = False) -> None:
        def work():
            self.api.transfer_playback(device_id, play=start_playing)
            self._clear_error()
            self.refresh_now()

        self.submit(work)

    def _run_playback(self, call: Callable[[], None]) -> None:
        """Run a player command, then reconcile as soon as Spotify has acted."""
        call()
        self._clear_error()
        if self.command_settle_seconds:
            time.sleep(self.command_settle_seconds)
        self.refresh_now()

    # -- misc -------------------------------------------------------------

    def open_in_spotify(self, url_or_uri: str | None) -> bool:
        """Hand a Spotify link to the desktop, preferring GIO over a browser."""
        if not url_or_uri:
            return False

        resource = parse_resource(url_or_uri)
        target = resource.external_url if resource else url_or_uri

        try:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(target, None)
            return True
        except Exception:  # noqa: BLE001 - no GTK, or no handler registered
            pass

        try:
            import webbrowser

            return webbrowser.open(target)
        except Exception:  # noqa: BLE001
            log.warning("Spotify: could not open the link")
            return False


def _track_uri(state: PlaybackState | None) -> str | None:
    track = state.track if state else None
    return track.uri if track else None


def _device_signature(devices: list[SpotifyDevice]) -> tuple:
    return tuple((device.id, device.name, device.is_active, device.volume_percent) for device in devices)


def _has_visible_change(previous: PlaybackState, current: PlaybackState) -> bool:
    """Whether anything an action draws has actually changed.

    Progress alone is excluded: it is interpolated locally, and actions that
    show it redraw on their own tick rather than on every poll.
    """
    return (
        _track_uri(previous) != _track_uri(current)
        or previous.is_playing != current.is_playing
        or previous.shuffle != current.shuffle
        or previous.repeat_mode != current.repeat_mode
        or previous.volume_percent != current.volume_percent
        or previous.has_playback != current.has_playback
        or previous.context_uri != current.context_uri
        or (previous.device.id if previous.device else None) != (current.device.id if current.device else None)
        or previous.disallowed_actions != current.disallowed_actions
    )
