"""One scheduler for every scrolling label in the plugin.

A thread per action would mean a dozen timers fighting over the deck; instead
one low-rate ticker redraws only the actions whose text actually overflows. The
offset itself is a pure function of elapsed time, so the animation can be tested
without running the thread at all.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

#: Deliberately low. Scrolling text is readable at this rate and it leaves the
#: deck's update budget for everything else.
DEFAULT_FPS = 8

DEFAULT_SPEED_PX_PER_SECOND = 32.0
HOLD_SECONDS = 0.75


def marquee_offset(
    elapsed: float,
    overflow_px: float,
    speed_px_per_second: float = DEFAULT_SPEED_PX_PER_SECOND,
    hold_seconds: float = HOLD_SECONDS,
) -> int:
    """Pixels to shift text left at `elapsed` seconds into the cycle.

    hold at the start, scroll to the end, hold there, then start over.
    """
    if overflow_px <= 0:
        return 0

    speed = max(1.0, speed_px_per_second)
    scroll_seconds = overflow_px / speed
    cycle = hold_seconds * 2 + scroll_seconds
    position = elapsed % cycle

    if position < hold_seconds:
        return 0
    if position < hold_seconds + scroll_seconds:
        return int(round((position - hold_seconds) * speed))
    return int(round(overflow_px))


@dataclass
class _Entry:
    callback: Callable[[], None]
    overflow: float = 0.0
    started_at: float = field(default_factory=time.monotonic)
    text: str = ""


class MarqueeScheduler:
    def __init__(self, fps: int = DEFAULT_FPS, speed_px_per_second: float = DEFAULT_SPEED_PX_PER_SECOND):
        self.fps = max(1, int(fps))
        self.speed = speed_px_per_second

        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = True

    # -- configuration ----------------------------------------------------

    def configure(self, enabled: bool = True, speed_px_per_second: float | None = None) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if speed_px_per_second:
                self.speed = float(speed_px_per_second)
        self._wake.set()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # -- registration -----------------------------------------------------

    def register(self, key: str, callback: Callable[[], None]) -> None:
        with self._lock:
            self._entries[key] = _Entry(callback=callback)
        self.start()

    def unregister(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def set_overflow(self, key: str, overflow_px: float, text: str = "") -> None:
        """Report how far the text exceeds its window, from the renderer.

        A changed text restarts the cycle, so a new song title is always read
        from its beginning rather than mid-scroll.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            if text != entry.text:
                entry.text = text
                entry.started_at = time.monotonic()
            entry.overflow = max(0.0, float(overflow_px))
        if overflow_px > 0:
            self._wake.set()

    def offset(self, key: str) -> int:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or not self._enabled or entry.overflow <= 0:
                return 0
            elapsed = time.monotonic() - entry.started_at
            speed = self.speed
            overflow = entry.overflow
        return marquee_offset(elapsed, overflow, speed)

    def reset(self, key: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.started_at = time.monotonic()
                entry.overflow = 0.0

    def reset_all(self) -> None:
        with self._lock:
            now = time.monotonic()
            for entry in self._entries.values():
                entry.started_at = now

    # -- the ticker -------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopping.is_set():
                return
            self._thread = threading.Thread(target=self._loop, name="spotify-marquee", daemon=True)
            thread = self._thread
        thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            self._entries.clear()

    def _loop(self) -> None:
        frame_time = 1.0 / self.fps

        while not self._stopping.is_set():
            with self._lock:
                enabled = self._enabled
                active = [entry for entry in self._entries.values() if entry.overflow > 0] if enabled else []

            if not active:
                # Nothing is scrolling: idle cheaply until something is.
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue

            for entry in active:
                try:
                    entry.callback()
                except Exception:  # noqa: BLE001 - a failing redraw must not stop the rest
                    pass

            self._wake.wait(timeout=frame_time)
            self._wake.clear()
