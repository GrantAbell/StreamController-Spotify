"""Bounded caches. Nothing here grows without limit.

The paged cache deliberately does no fetching of its own: it reports which page
is missing and the manager's worker fetches it. That keeps "which page do I need
next" testable, and keeps HTTP out of anything a dial event can reach.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LruCache(Generic[K, V]):
    """A small thread-safe LRU. Used for artwork and other decoded objects."""

    def __init__(self, max_items: int = 24):
        self._max_items = max(1, max_items)
        self._items: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            if key not in self._items:
                return default
            self._items.move_to_end(key)
            return self._items[key]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._max_items:
                self._items.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class TtlCache(Generic[K, V]):
    """Values that are worth remembering but must not be trusted forever.

    Used for liked-state per track URI and for resolved context details, both of
    which can be changed from outside StreamController.
    """

    def __init__(self, ttl_seconds: float = 300.0, max_items: int = 256):
        self._ttl = ttl_seconds
        self._max_items = max(1, max_items)
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K, default: V | None = None) -> V | None:
        now = time.monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return default
            expires_at, value = entry
            if expires_at <= now:
                del self._items[key]
                return default
            self._items.move_to_end(key)
            return value

    def put(self, key: K, value: V, ttl_seconds: float | None = None) -> None:
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        with self._lock:
            self._items[key] = (time.monotonic() + ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_items:
                self._items.popitem(last=False)

    def invalidate(self, key: K) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class PagedCache(Generic[V]):
    """A lazily-filled window over a paginated Spotify collection.

    A library of several thousand saved tracks must not be downloaded to answer
    a single dial turn, so items arrive one page at a time and the cache reports
    which page the current position needs next.
    """

    def __init__(self, page_size: int = 50, prefetch_margin: int = 10):
        self.page_size = max(1, page_size)
        self.prefetch_margin = max(0, prefetch_margin)

        self._items: dict[int, V] = {}
        self._total: int | None = None
        self._requested_offsets: set[int] = set()
        self._lock = threading.RLock()

    # -- reading ----------------------------------------------------------

    @property
    def total(self) -> int | None:
        """How many items exist, or None until the first page arrives."""
        with self._lock:
            return self._total

    @property
    def loaded_count(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, index: int) -> V | None:
        with self._lock:
            return self._items.get(index)

    def has(self, index: int) -> bool:
        with self._lock:
            return index in self._items

    def is_empty(self) -> bool:
        """True only when Spotify has confirmed the collection is empty."""
        with self._lock:
            return self._total == 0

    # -- navigation -------------------------------------------------------

    def step(self, index: int, delta: int, wrap: bool = True) -> int:
        """Move the selection, honouring the known total.

        Before the total is known, movement is still allowed forwards so the
        first turn of a dial is not swallowed while page one is in flight.
        """
        with self._lock:
            total = self._total

        if total is None:
            return max(0, index + delta)
        if total <= 0:
            return 0

        target = index + delta
        if wrap:
            return target % total
        return max(0, min(total - 1, target))

    def missing_offsets(self, index: int) -> list[int]:
        """Page offsets needed to show `index` and the items just past it."""
        with self._lock:
            total = self._total
            wanted: list[int] = []

            for position in (index, index + self.prefetch_margin):
                if position < 0:
                    continue
                if total is not None and position >= total:
                    continue
                offset = (position // self.page_size) * self.page_size
                if offset in self._requested_offsets:
                    continue
                if all((offset + i) in self._items for i in range(self.page_size)):
                    continue
                if offset not in wanted:
                    wanted.append(offset)

            return wanted

    def mark_requested(self, offset: int) -> None:
        with self._lock:
            self._requested_offsets.add(offset)

    def unmark_requested(self, offset: int) -> None:
        """Allow a failed page to be retried."""
        with self._lock:
            self._requested_offsets.discard(offset)

    # -- filling ----------------------------------------------------------

    def apply_page(self, offset: int, items: list[V], total: int | None) -> None:
        with self._lock:
            for position, item in enumerate(items):
                self._items[offset + position] = item
            if total is not None:
                self._total = total
            elif self._total is None:
                self._total = offset + len(items)
            self._requested_offsets.add(offset)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total = None
            self._requested_offsets.clear()

    def snapshot_range(self, start: int, count: int) -> list[V | None]:
        with self._lock:
            return [self._items.get(start + i) for i in range(count)]


class Debouncer:
    """Collapses repeated requests for the same work into one run.

    Rapid dial turns would otherwise queue one HTTP request per detent; this is
    what turns 50→55→60→65→70 into 50→70 while still sending the final value.
    """

    def __init__(self, interval: float = 0.15):
        self.interval = interval
        self._last_run: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_run(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._last_run.get(key)
            if last is not None and (now - last) < self.interval:
                return False
            self._last_run[key] = now
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._last_run.pop(key, None)


def call_safely(callback: Callable[[], None], on_error: Callable[[Exception], None] | None = None) -> None:
    """Run a listener without letting one bad listener break the fan-out."""
    try:
        callback()
    except Exception as error:  # noqa: BLE001 - listeners are third-party code
        if on_error is not None:
            on_error(error)
