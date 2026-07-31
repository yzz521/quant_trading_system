"""The central event engine.

A single-threaded event loop. Handlers are registered per :class:`EventType`;
when an event is popped from the queue every registered handler for that
type is invoked in registration order. Handlers may push new events (e.g.
the strategy handler emits ``SignalEvent`` after receiving ``MarketEvent``),
which keeps the loop running until the queue drains — exactly the behaviour
backtesting needs, and live trading just keeps the loop alive forever.

Design notes
------------
* The queue is a plain ``collections.deque`` — for the backtest this is the
  fastest option and avoids threading complexity. A live engine that needs
  to receive data from a network thread should wrap :meth:`put` in a lock
  or swap the queue for ``queue.Queue``.
* ``run_once`` drains the current queue and returns. Backtests call this
  once per bar. ``run`` blocks until :meth:`stop` is called and is meant
  for live/paper trading.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque

from ..utils import get_logger
from .event import Event, EventType

EventHandler = Callable[[Event], None]


class EventEngine:
    def __init__(self) -> None:
        self._queue: Deque[Event] = deque()
        self._handlers: dict[EventType, list[EventHandler]] = {
            et: [] for et in EventType
        }
        self._running = False
        self._events_processed = 0
        self.log = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # Handler registration
    # ------------------------------------------------------------------ #
    def register(self, event_type: EventType, handler: EventHandler) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unregister(self, event_type: EventType, handler: EventHandler) -> None:
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    # ------------------------------------------------------------------ #
    # Queue operations
    # ------------------------------------------------------------------ #
    def put(self, event: Event) -> None:
        """Push an event onto the queue."""
        self._queue.append(event)

    def put_left(self, event: Event) -> None:
        """Push with high priority (processed next). Used for risk stops."""
        self._queue.appendleft(event)

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def events_processed(self) -> int:
        return self._events_processed

    # ------------------------------------------------------------------ #
    # Loop control
    # ------------------------------------------------------------------ #
    def _dispatch(self, event: Event) -> None:
        for handler in list(self._handlers.get(event.type, [])):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - keep the loop alive
                self.log.exception("Handler %r failed on event %r", handler, event)
        self._events_processed += 1

    def run_once(self) -> int:
        """Drain the queue, processing every event currently pending.

        Returns the number of events processed. New events pushed by handlers
        are processed in the same call, so this returns only when the queue
        is fully empty again.
        """
        processed = 0
        while self._queue:
            event = self._queue.popleft()
            self._dispatch(event)
            processed += 1
        return processed

    def run(self, poll_interval: float = 0.1) -> None:
        """Blocking loop for live/paper trading. Stops on :meth:`stop`."""
        self._running = True
        self.log.info("EventEngine started (live/paper mode)")
        try:
            while self._running:
                if self._queue:
                    event = self._queue.popleft()
                    self._dispatch(event)
                else:
                    time.sleep(poll_interval)
        finally:
            self.log.info("EventEngine stopped after %d events", self._events_processed)

    def stop(self) -> None:
        self._running = False

    def reset(self) -> None:
        self._queue.clear()
        self._events_processed = 0
