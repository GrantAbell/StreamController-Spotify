"""The shared artwork cache: bounded, off-thread, and forgiving of failures."""

from __future__ import annotations

import io
import time

from PIL import Image

from spotify_essentials.spotify.artwork import ArtworkCache


class _Response:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content


def _png(size=(300, 300), colour=(10, 200, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


class _Session:
    def __init__(self, failing: set[str] | None = None):
        self.requested: list[str] = []
        self.failing = failing or set()

    def get(self, url, timeout=None):
        self.requested.append(url)
        if url in self.failing:
            return _Response(404, b"")
        return _Response(200, _png())

    def close(self):
        pass


def _wait_for(predicate, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_first_request_returns_nothing_and_fetches_in_the_background():
    session = _Session()
    loaded = []
    cache = ArtworkCache(session=session, on_loaded=loaded.append)
    try:
        # A renderer must never block on a download.
        assert cache.get("https://i.example/a.jpg") is None
        assert _wait_for(lambda: loaded)

        image = cache.get("https://i.example/a.jpg")
        assert isinstance(image, Image.Image)
    finally:
        cache.shutdown()


def test_the_same_url_is_only_downloaded_once():
    session = _Session()
    cache = ArtworkCache(session=session)
    try:
        for _ in range(5):
            cache.get("https://i.example/a.jpg")
        assert _wait_for(lambda: session.requested)
        time.sleep(0.05)

        assert session.requested.count("https://i.example/a.jpg") == 1
    finally:
        cache.shutdown()


def test_the_cache_is_bounded():
    session = _Session()
    cache = ArtworkCache(session=session, max_items=3)
    try:
        for index in range(8):
            cache.prefetch(f"https://i.example/{index}.jpg")
        assert _wait_for(lambda: len(session.requested) == 8)
        time.sleep(0.1)

        assert len(cache._images) <= 3
    finally:
        cache.shutdown()


def test_a_failed_image_is_not_retried_forever():
    url = "https://i.example/missing.jpg"
    session = _Session(failing={url})
    cache = ArtworkCache(session=session)
    try:
        cache.get(url)
        assert _wait_for(lambda: session.requested)
        time.sleep(0.05)

        for _ in range(3):
            assert cache.get(url) is None
        time.sleep(0.05)

        assert session.requested.count(url) == 1
    finally:
        cache.shutdown()


def test_no_url_is_never_a_request():
    session = _Session()
    cache = ArtworkCache(session=session)
    try:
        assert cache.get(None) is None
        assert cache.get("") is None
        assert session.requested == []
    finally:
        cache.shutdown()


def test_images_are_decoded_down_to_a_display_size():
    session = _Session()
    cache = ArtworkCache(session=session)
    try:
        cache.get("https://i.example/a.jpg")
        assert _wait_for(lambda: cache.get("https://i.example/a.jpg") is not None)

        image = cache.get("https://i.example/a.jpg")
        assert max(image.size) <= 320
    finally:
        cache.shutdown()


def test_shutdown_stops_accepting_work():
    session = _Session()
    cache = ArtworkCache(session=session)
    cache.shutdown()

    cache.prefetch("https://i.example/after.jpg")
    time.sleep(0.05)
    assert session.requested == []
