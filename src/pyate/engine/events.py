"""Thread-safe, synchronous/asynchronous event bus used by every layer.

Subscribers are plain callables; a failing subscriber never breaks the run,
its exception is routed to :attr:`EventBus.on_error` (logging by default).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

_LOG = logging.getLogger("pyate.events")

__all__ = ["EventKind", "Event", "EventBus"]


class EventKind(str, Enum):
    """Well known topics.  Plain strings are accepted as well (wildcards ok)."""

    LOT_START = "LOT_START"
    LOT_END = "LOT_END"
    UNIT_START = "UNIT_START"
    UNIT_END = "UNIT_END"
    WAFER_START = "WAFER_START"
    WAFER_END = "WAFER_END"
    TOUCHDOWN = "TOUCHDOWN"
    PART_START = "PART_START"
    PART_END = "PART_END"
    TEST_START = "TEST_START"
    TEST_RESULT = "TEST_RESULT"
    BIN_RESULT = "BIN_RESULT"
    FLOW_STEP = "FLOW_STEP"
    PROGRESS = "PROGRESS"
    LOG = "LOG"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Event:
    """An immutable event envelope."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    timestamp: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class EventBus:
    """Fan-out event bus.

    ``publish`` is synchronous by default: every listener runs in the calling
    thread before ``publish`` returns.  Call :meth:`start_async` to move the
    dispatch onto a dedicated worker thread (used by the GUI and by streaming
    datalog sinks that must not slow the tester down).
    """

    def __init__(self, *, max_queue: int = 200_000, async_dispatch: bool = False) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, list[Callable[[Event], None]]] = {}
        self._seq = 0
        self._queue: queue.Queue[Event | None] = queue.Queue(maxsize=max_queue)
        self._worker: threading.Thread | None = None
        self._draining = threading.Event()
        self._error_count = 0
        self.on_error: Callable[[Event, BaseException], None] = self._default_on_error
        if async_dispatch:
            self.start_async()

    # ------------------------------------------------------------------ subs
    def subscribe(self, kind: str | EventKind, callback: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe ``callback`` to ``kind`` (``"*"`` matches everything)."""
        key = kind.value if isinstance(kind, EventKind) else str(kind)
        with self._lock:
            self._subs.setdefault(key, []).append(callback)

        def _unsubscribe() -> None:
            self.unsubscribe(key, callback)

        return _unsubscribe

    def unsubscribe(self, kind: str | EventKind, callback: Callable[[Event], None]) -> None:
        """Remove a previously registered callback (idempotent)."""
        key = kind.value if isinstance(kind, EventKind) else str(kind)
        with self._lock:
            callbacks = self._subs.get(key)
            if callbacks and callback in callbacks:
                callbacks.remove(callback)
                if not callbacks:
                    self._subs.pop(key, None)

    def clear(self) -> None:
        """Drop every subscription."""
        with self._lock:
            self._subs.clear()

    # --------------------------------------------------------------- publish
    def publish(self, kind: str | EventKind, data: dict[str, Any] | None = None, **kwargs: Any) -> Event:
        """Emit an event.  Returns the envelope that was produced."""
        topic = kind.value if isinstance(kind, EventKind) else str(kind)
        payload = dict(data or {})
        if kwargs:
            payload.update(kwargs)
        with self._lock:
            self._seq += 1
            event = Event(kind=topic, data=payload, seq=self._seq)
        if self._worker is not None:
            try:
                self._queue.put_nowait(event)
            except queue.Full:  # pragma: no cover - defensive
                self._dispatch(event)
        else:
            self._dispatch(event)
        return event

    def _dispatch(self, event: Event) -> None:
        with self._lock:
            callbacks = list(self._subs.get(event.kind, ()))
            if event.kind != "*":
                callbacks += list(self._subs.get("*", ()))
        for callback in callbacks:
            try:
                callback(event)
            except Exception as exc:  # noqa: BLE001 - listener isolation is the point
                self._error_count += 1
                try:
                    self.on_error(event, exc)
                except Exception:  # pragma: no cover - never propagate
                    _LOG.exception("event error handler failed")

    @staticmethod
    def _default_on_error(event: Event, exc: BaseException) -> None:
        _LOG.warning("subscriber failed for %s: %s", event.kind, exc, exc_info=exc)

    # ----------------------------------------------------------------- async
    def start_async(self) -> None:
        """Switch to a single background dispatch thread."""
        if self._worker is not None:
            return
        self._draining.clear()
        self._worker = threading.Thread(target=self._run_worker, name="pyate-events", daemon=True)
        self._worker.start()

    def _run_worker(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                self._queue.task_done()
                break
            try:
                self._dispatch(event)
            finally:
                self._queue.task_done()
        self._draining.set()

    def stop_async(self, timeout: float = 5.0) -> None:
        """Drain the queue and stop the worker thread."""
        worker = self._worker
        if worker is None:
            return
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:  # pragma: no cover
            pass
        worker.join(timeout)
        self._worker = None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until every queued event has been dispatched."""
        if self._worker is None:
            return True
        done = threading.Event()

        def _wait() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=_wait, name="pyate-events-wait", daemon=True).start()
        return done.wait(timeout)

    @property
    def error_count(self) -> int:
        """Number of subscriber exceptions swallowed so far."""
        return self._error_count
