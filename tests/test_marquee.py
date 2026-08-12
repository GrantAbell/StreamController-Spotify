"""Marquee timing and the scheduler's bookkeeping.

The offset is a pure function of elapsed time, so the animation curve is tested
directly instead of by watching a thread.
"""

from __future__ import annotations

import time

from spotify_essentials.rendering.marquee import (
    DEFAULT_SPEED_PX_PER_SECOND,
    HOLD_SECONDS,
    MarqueeScheduler,
    marquee_offset,
)

OVERFLOW = 64.0  # two seconds of scrolling at the default speed
SCROLL_SECONDS = OVERFLOW / DEFAULT_SPEED_PX_PER_SECOND


def test_text_that_fits_never_moves():
    for elapsed in (0.0, 1.0, 10.0):
        assert marquee_offset(elapsed, overflow_px=0) == 0


def test_the_cycle_holds_scrolls_holds_then_repeats():
    # Held at the start, so the beginning of a title is readable.
    assert marquee_offset(0.0, OVERFLOW) == 0
    assert marquee_offset(HOLD_SECONDS - 0.01, OVERFLOW) == 0

    # Scrolling.
    midway = marquee_offset(HOLD_SECONDS + SCROLL_SECONDS / 2, OVERFLOW)
    assert 0 < midway < OVERFLOW

    # Held at the end, so the end is readable too.
    assert marquee_offset(HOLD_SECONDS + SCROLL_SECONDS, OVERFLOW) == int(OVERFLOW)
    assert marquee_offset(HOLD_SECONDS + SCROLL_SECONDS + HOLD_SECONDS - 0.01, OVERFLOW) == int(OVERFLOW)

    # Then back to the beginning.
    cycle = HOLD_SECONDS * 2 + SCROLL_SECONDS
    assert marquee_offset(cycle, OVERFLOW) == 0


def test_scrolling_never_overshoots():
    for step in range(0, 200):
        offset = marquee_offset(step * 0.05, OVERFLOW)
        assert 0 <= offset <= OVERFLOW


def test_speed_changes_the_duration_not_the_distance():
    slow = marquee_offset(HOLD_SECONDS + 1.0, OVERFLOW, speed_px_per_second=16)
    fast = marquee_offset(HOLD_SECONDS + 1.0, OVERFLOW, speed_px_per_second=64)
    assert fast > slow


# -- the scheduler ---------------------------------------------------------


def test_registered_actions_only_scroll_when_they_overflow():
    scheduler = MarqueeScheduler()
    try:
        redraws = []
        scheduler.register("key-1", lambda: redraws.append(1))

        assert scheduler.offset("key-1") == 0

        scheduler.set_overflow("key-1", 100.0, text="A long title")
        time.sleep(HOLD_SECONDS + 0.3)
        assert scheduler.offset("key-1") > 0
    finally:
        scheduler.stop()


def test_changing_text_restarts_the_cycle():
    scheduler = MarqueeScheduler()
    try:
        scheduler.register("key-1", lambda: None)
        scheduler.set_overflow("key-1", 100.0, text="First song")
        time.sleep(HOLD_SECONDS + 0.2)
        assert scheduler.offset("key-1") > 0

        # A new track has to be read from its beginning.
        scheduler.set_overflow("key-1", 100.0, text="Second song")
        assert scheduler.offset("key-1") == 0
    finally:
        scheduler.stop()


def test_unregistering_stops_the_offsets():
    scheduler = MarqueeScheduler()
    try:
        scheduler.register("key-1", lambda: None)
        scheduler.set_overflow("key-1", 100.0, text="Title")
        scheduler.unregister("key-1")

        assert scheduler.offset("key-1") == 0
        assert scheduler.offset("never-registered") == 0
    finally:
        scheduler.stop()


def test_disabling_the_marquee_freezes_every_action():
    scheduler = MarqueeScheduler()
    try:
        scheduler.register("key-1", lambda: None)
        scheduler.set_overflow("key-1", 100.0, text="Title")
        time.sleep(HOLD_SECONDS + 0.2)

        scheduler.configure(enabled=False)
        assert scheduler.offset("key-1") == 0
        assert not scheduler.enabled
    finally:
        scheduler.stop()


def test_reset_all_restarts_every_cycle():
    scheduler = MarqueeScheduler()
    try:
        scheduler.register("key-1", lambda: None)
        scheduler.set_overflow("key-1", 100.0, text="Title")
        time.sleep(HOLD_SECONDS + 0.2)
        assert scheduler.offset("key-1") > 0

        scheduler.reset_all()
        assert scheduler.offset("key-1") == 0
    finally:
        scheduler.stop()


def test_the_ticker_redraws_only_scrolling_actions():
    scheduler = MarqueeScheduler(fps=20)
    try:
        scrolling = []
        still = []
        scheduler.register("scrolling", lambda: scrolling.append(1))
        scheduler.register("still", lambda: still.append(1))
        scheduler.set_overflow("scrolling", 200.0, text="Long")
        scheduler.set_overflow("still", 0.0, text="Short")

        time.sleep(0.35)

        assert len(scrolling) >= 2
        assert still == [], "a key whose text fits must not be redrawn at all"
    finally:
        scheduler.stop()


def test_stopping_the_scheduler_ends_its_thread():
    scheduler = MarqueeScheduler()
    scheduler.register("key-1", lambda: None)
    scheduler.set_overflow("key-1", 100.0, text="Title")
    time.sleep(0.1)

    scheduler.stop()
    assert scheduler._thread is None
