"""Repeat-while-held helper, used by the hold-to-seek actions.

StreamController delivers HOLD_START and HOLD_STOP but nothing in between, so
an action that should keep seeking while a key is down has to run its own
timer. This is a chained `threading.Timer` rather than a loop with a sleep, so
nothing is left spinning when the key comes back up.
"""

from __future__ import annotations

import threading
from typing import Callable


class HoldRepeater:
    def __init__(self, interval: float, callback: Callable[[], None]):
        self.interval = max(0.05, float(interval))
        self._callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, interval: float | None = None) -> None:
        """Fire once immediately, then keep firing until stopped."""
        if interval is not None:
            self.interval = max(0.05, float(interval))

        with self._lock:
            if self._running:
                return
            self._running = True

        self._fire()

    def _fire(self) -> None:
        with self._lock:
            if not self._running:
                return

        try:
            self._callback()
        except Exception:  # noqa: BLE001 - a failed repeat must not strand the timer
            pass

        with self._lock:
            if not self._running:
                return
            self._timer = threading.Timer(self.interval, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
