"""Bounded caches, and the paging rules the browsing dials depend on."""

from __future__ import annotations

import time

from spotify_essentials.spotify.cache import Debouncer, LruCache, PagedCache, TtlCache


def test_lru_evicts_the_least_recently_used():
    cache: LruCache[str, int] = LruCache(max_items=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # touching "a" makes "b" the oldest
    cache.put("c", 3)

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert len(cache) == 2


def test_ttl_cache_expires():
    cache: TtlCache[str, bool] = TtlCache(ttl_seconds=0.05)
    cache.put("liked", True)
    assert cache.get("liked") is True

    time.sleep(0.06)
    assert cache.get("liked") is None


def test_ttl_invalidate():
    cache: TtlCache[str, bool] = TtlCache()
    cache.put("x", True)
    cache.invalidate("x")
    assert cache.get("x") is None


# -- paged collection ------------------------------------------------------


def _cache(page_size=50, margin=10) -> PagedCache[str]:
    return PagedCache(page_size=page_size, prefetch_margin=margin)


def test_empty_collection_is_distinguishable_from_unloaded():
    cache = _cache()
    assert cache.total is None
    assert not cache.is_empty()

    cache.apply_page(0, [], 0)
    assert cache.total == 0
    assert cache.is_empty()


def test_first_page_is_requested_for_index_zero():
    cache = _cache()
    assert cache.missing_offsets(0) == [0]


def test_a_loaded_page_is_not_requested_again():
    cache = _cache()
    cache.apply_page(0, [f"item{i}" for i in range(50)], 1268)

    assert cache.missing_offsets(0) == []
    assert cache.get(7) == "item7"


def test_approaching_the_end_prefetches_the_next_page():
    cache = _cache()
    cache.apply_page(0, [f"item{i}" for i in range(50)], 1268)

    # Still comfortably inside page one.
    assert cache.missing_offsets(20) == []
    # Within the prefetch margin of page two.
    assert cache.missing_offsets(45) == [50]


def test_prefetch_stops_at_the_end_of_the_collection():
    cache = _cache()
    cache.apply_page(0, [f"item{i}" for i in range(10)], 10)
    assert cache.missing_offsets(9) == []


def test_navigation_wraps_by_default():
    cache = _cache()
    cache.apply_page(0, ["a", "b", "c"], 3)

    assert cache.step(2, 1) == 0
    assert cache.step(0, -1) == 2


def test_navigation_can_be_clamped_instead():
    cache = _cache()
    cache.apply_page(0, ["a", "b", "c"], 3)

    assert cache.step(2, 1, wrap=False) == 2
    assert cache.step(0, -1, wrap=False) == 0


def test_navigation_before_the_total_is_known_still_moves_forward():
    # The first dial turn must not be swallowed while page one is in flight.
    cache = _cache()
    assert cache.step(0, 1) == 1
    assert cache.step(0, -1) == 0


def test_single_item_collection():
    cache = _cache()
    cache.apply_page(0, ["only"], 1)

    assert cache.step(0, 1) == 0
    assert cache.step(0, -1) == 0


def test_failed_page_can_be_retried():
    cache = _cache()
    cache.mark_requested(0)
    assert cache.missing_offsets(0) == []

    cache.unmark_requested(0)
    assert cache.missing_offsets(0) == [0]


def test_clear_forgets_everything_including_the_total():
    cache = _cache()
    cache.apply_page(0, ["a"], 1)
    cache.clear()

    assert cache.total is None
    assert cache.get(0) is None
    assert cache.missing_offsets(0) == [0]


def test_debouncer_collapses_rapid_repeats():
    debouncer = Debouncer(interval=10.0)
    now = time.monotonic()

    assert debouncer.should_run("volume", now)
    assert not debouncer.should_run("volume", now + 1)
    assert debouncer.should_run("seek", now + 1)
    assert debouncer.should_run("volume", now + 11)
