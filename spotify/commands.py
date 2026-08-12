"""The work queue that keeps Spotify calls off StreamController's threads.

Two kinds of work go in. Discrete commands — next track, like, transfer — are
distinct user intents, and every one of them is sent. Continuous commands —
volume, seek — describe a target value, so a newer one makes an older one
pointless and replaces it in place. That is what stops a fast dial spin from
queueing forty HTTP requests.
"""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict
from typing import Callable

#: Coalescing keys for the continuous properties.
KEY_VOLUME = "volume"
KEY_SEEK = "seek"


class CommandQueue:
    def __init__(self):
        self._items: OrderedDict[str, Callable[[], None]] = OrderedDict()
        self._condition = threading.Condition()
        self._counter = itertools.count()
        self._closed = False

    def submit(self, work: Callable[[], None], coalesce_key: str | None = None) -> None:
        """Queue work. With a key, it replaces any pending work for that key."""
        with self._condition:
            if self._closed:
                return
            if coalesce_key is None:
                key = f"discrete:{next(self._counter)}"
            else:
                key = f"latest:{coalesce_key}"
            # Assigning an existing key keeps its queue position, so a volume
            # change does not jump ahead of a pause the user pressed first.
            self._items[key] = work
            self._condition.notify()

    def take(self, timeout: float = 0.25) -> Callable[[], None] | None:
        with self._condition:
            if not self._items and not self._closed:
                self._condition.wait(timeout)
            if self._items:
                _, work = self._items.popitem(last=False)
                return work
            return None

    def pending(self) -> int:
        with self._condition:
            return len(self._items)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._items.clear()
            self._condition.notify_all()

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed
