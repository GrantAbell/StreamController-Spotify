"""Album and playlist artwork: fetched once, shared by every action.

Two actions showing the same album must not download the same image twice, and
no download may ever happen on the thread that is drawing a key. Requests are
made on a small worker pool; renderers only ever read what is already decoded.
"""

from __future__ import annotations

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests
from PIL import Image

from .cache import LruCache
from .log import debug, log

#: Bounded on purpose — this is a display cache, not an archive of Spotify art.
MAX_CACHED_IMAGES = 24

#: Decoded once at this size; every layout scales down from it.
DECODE_SIZE = (320, 320)

_TIMEOUT = (3.05, 6)


class ArtworkCache:
    def __init__(
        self,
        session: requests.Session | None = None,
        max_items: int = MAX_CACHED_IMAGES,
        max_workers: int = 2,
        on_loaded: Callable[[str], None] | None = None,
    ):
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._images: LruCache[str, Image.Image] = LruCache(max_items)
        self._failed: set[str] = set()
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._on_loaded = on_loaded
        self._shutdown = False
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="spotify-artwork")

    def get(self, url: str | None) -> Image.Image | None:
        """The decoded image if it is already here, else None — never blocks.

        A miss schedules the fetch and returns None so the caller can draw its
        placeholder immediately; the listener redraws when the image lands.
        """
        if not url:
            return None

        image = self._images.get(url)
        if image is not None:
            return image

        self.prefetch(url)
        return None

    def prefetch(self, url: str | None) -> None:
        if not url:
            return

        with self._lock:
            if self._shutdown or url in self._in_flight or url in self._failed:
                return
            if url in self._images:
                return
            self._in_flight.add(url)

        try:
            self._pool.submit(self._fetch, url)
        except RuntimeError:
            # Pool already shut down.
            with self._lock:
                self._in_flight.discard(url)

    def _fetch(self, url: str) -> None:
        try:
            response = self._session.get(url, timeout=_TIMEOUT)
            if response.status_code >= 400:
                raise OSError(f"HTTP {response.status_code}")

            with Image.open(io.BytesIO(response.content)) as raw:
                image = raw.convert("RGB")
                image.thumbnail(DECODE_SIZE, Image.LANCZOS)
                # Detach from the file object before the context closes.
                image = image.copy()

            self._images.put(url, image)
            debug("Spotify: artwork loaded")

            if self._on_loaded is not None:
                self._on_loaded(url)

        except Exception as error:  # noqa: BLE001 - one bad image must not matter
            # Remembered as failed so a broken URL is not retried on every draw;
            # cleared whenever the caches are reset.
            with self._lock:
                self._failed.add(url)
            log.info(f"Artwork fetch failed ({error.__class__.__name__})")
        finally:
            with self._lock:
                self._in_flight.discard(url)

    def clear(self) -> None:
        self._images.clear()
        with self._lock:
            self._failed.clear()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._owns_session:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
        self.clear()
