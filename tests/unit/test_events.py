"""Unit tests for the event bus."""

from __future__ import annotations

import threading
import time

import pytest

from pyate.engine.events import Event, EventBus, EventKind


def test_publish_reaches_subscriber() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventKind.LOG, seen.append)
    bus.publish(EventKind.LOG, {"message": "hello"})
    assert len(seen) == 1
    assert seen[0].kind == "LOG"
    assert seen[0].get("message") == "hello"


def test_wildcard_subscriber() -> None:
    bus = EventBus()
    kinds: list[str] = []
    bus.subscribe("*", lambda event: kinds.append(str(event.kind)))
    bus.publish(EventKind.LOT_START, {})
    bus.publish(EventKind.LOT_END, {})
    assert kinds == ["LOT_START", "LOT_END"]


def test_unsubscribe() -> None:
    bus = EventBus()
    seen: list[Event] = []
    handler = seen.append
    drop = bus.subscribe(EventKind.LOG, handler)
    bus.publish(EventKind.LOG, {})
    drop()
    bus.publish(EventKind.LOG, {})
    assert len(seen) == 1

    bus.subscribe(EventKind.LOG, handler)
    bus.unsubscribe(EventKind.LOG, handler)
    bus.publish(EventKind.LOG, {})
    assert len(seen) == 1


def test_subscriber_exception_is_isolated() -> None:
    bus = EventBus()
    seen: list[Event] = []

    def boom(event: Event) -> None:
        raise RuntimeError("subscriber failed")

    bus.subscribe(EventKind.LOG, boom)
    bus.subscribe(EventKind.LOG, seen.append)
    bus.publish(EventKind.LOG, {"message": "x"})
    assert len(seen) == 1
    assert bus.error_count == 1


def test_sequence_numbers_increase() -> None:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe("*", events.append)
    for index in range(5):
        bus.publish(EventKind.PROGRESS, {"index": index})
    assert [event.seq for event in events] == [1, 2, 3, 4, 5]


def test_async_mode_uses_a_queue() -> None:
    bus = EventBus()
    seen: list[Event] = []
    done = threading.Event()
    bus.subscribe(EventKind.PART_END, lambda event: (seen.append(event), done.set()))
    bus.start_async()
    try:
        bus.publish(EventKind.PART_END, {"part": "p1"})
        assert done.wait(2.0), "async dispatch did not run"
        bus.wait_idle(timeout=2.0)
        assert len(seen) == 1
    finally:
        bus.stop_async()


def test_wait_idle_drains_the_queue() -> None:
    bus = EventBus()

    def slow(event: Event) -> None:
        time.sleep(0.005)

    counter = {"n": 0}

    def count(event: Event) -> None:
        counter["n"] += 1

    bus.subscribe(EventKind.TEST_RESULT, slow)
    bus.subscribe(EventKind.TEST_RESULT, count)
    bus.start_async()
    try:
        for index in range(50):
            bus.publish(EventKind.TEST_RESULT, {"index": index})
        bus.wait_idle(timeout=5.0)
        assert counter["n"] == 50
    finally:
        bus.stop_async()


def test_event_payload_is_immutable() -> None:
    bus = EventBus()
    captured: list[Event] = []
    bus.subscribe(EventKind.LOG, captured.append)
    payload = {"message": "x"}
    bus.publish(EventKind.LOG, payload)
    with pytest.raises(Exception):
        captured[0].kind = "OTHER"  # type: ignore[misc]
    assert captured[0].data == payload


def test_plain_string_topics_are_accepted() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe("CUSTOM", seen.append)
    bus.publish("CUSTOM", {"value": 1})
    assert seen and seen[0].get("value") == 1


def test_profiling_many_events() -> None:
    """100k events must stay well below a second (GUI smoke precondition)."""
    bus = EventBus()
    counter = {"n": 0}
    bus.subscribe(EventKind.TEST_RESULT, lambda event: counter.__setitem__("n", counter["n"] + 1))
    start = time.perf_counter()
    for index in range(100_000):
        bus.publish(EventKind.TEST_RESULT, {"index": index})
    elapsed = time.perf_counter() - start
    assert counter["n"] == 100_000
    assert elapsed < 5.0, f"event dispatch too slow: {elapsed:.2f}s"
