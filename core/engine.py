"""The central event engine.

Backtest: single-threaded ``deque`` (default, fastest).
Live/Paper: set ``thread_safe=True`` so a network/feed thread can ``put``
while the engine thread drains the queue.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable, Deque, Union

from ..utils import get_logger
from .event import Event, EventType

EventHandler = Callable[[Event], None]


class EventEngine:
    def __init__(self, thread_safe: bool = False) -> None:
        self.thread_safe = bool(thread_safe)
        if self.thread_safe:
            self._queue: Union[queue.Queue, Deque] = queue.Queue()
        else:
            self._queue = deque()
        self._handlers: dict[EventType, list[EventHandler]] = {
            et: [] for et in EventType
        }
        self._running = False
        self._events_processed = 0
        self._lock = threading.Lock()  # protects handler list mutations
        self.log = get_logger(self.__class__.__name__)

    def register(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unregister(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)

    def put(self, event: Event) -> None:
        if self.thread_safe:
            self._queue.put(event)  # type: ignore[union-attr]
        else:
            self._queue.append(event)  # type: ignore[union-attr]

    def put_left(self, event: Event) -> None:
        """High priority: only supported on deque (backtest). Thread-safe mode
        falls back to normal put (FIFO)."""
        if self.thread_safe:
            self._queue.put(event)  # type: ignore[union-attr]
        else:
            self._queue.appendleft(event)  # type: ignore[union-attr]

    @property
    def queue_size(self) -> int:
        if self.thread_safe:
            return self._queue.qsize()  # type: ignore[union-attr]
        return len(self._queue)  # type: ignore[union-attr]

    @property
    def events_processed(self) -> int:
        return self._events_processed

    def _dispatch(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event.type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                self.log.exception("Handler %r failed on event %r", handler, event)
        self._events_processed += 1

    def _pop(self, timeout: float | None = None) -> Event | None:
        if self.thread_safe:
            try:
                if timeout is None:
                    return self._queue.get_nowait()  # type: ignore[union-attr]
                return self._queue.get(timeout=timeout)  # type: ignore[union-attr]
            except queue.Empty:
                return None
        if self._queue:
            return self._queue.popleft()  # type: ignore[union-attr]
        return None

    def run_once(self) -> int:
        """Drain currently pending events (backtest-friendly)."""
        processed = 0
        if self.thread_safe:
            while True:
                event = self._pop(timeout=None)
                if event is None:
                    break
                self._dispatch(event)
                processed += 1
            return processed
        while self._queue:  # type: ignore[truthy-bool]
            event = self._queue.popleft()  # type: ignore[union-attr]
            self._dispatch(event)
            processed += 1
        return processed

    def run(self, poll_interval: float = 0.1) -> None:
        """Blocking loop for live/paper. Stops on :meth:`stop`."""
        self._running = True
        self.log.info(
            "EventEngine started (live/paper mode, thread_safe=%s)", self.thread_safe
        )
        try:
            while self._running:
                event = self._pop(timeout=poll_interval if self.thread_safe else None)
                if event is not None:
                    self._dispatch(event)
                elif not self.thread_safe:
                    time.sleep(poll_interval)
        finally:
            self.log.info("EventEngine stopped after %d events", self._events_processed)

    def stop(self) -> None:
        self._running = False
        # Drain remaining events so final fills are not lost
        try:
            self.run_once()
        except Exception:
            pass

    def reset(self) -> None:
        if self.thread_safe:
            while True:
                try:
                    self._queue.get_nowait()  # type: ignore[union-attr]
                except queue.Empty:
                    break
        else:
            self._queue.clear()  # type: ignore[union-attr]
        self._events_processed = 0
