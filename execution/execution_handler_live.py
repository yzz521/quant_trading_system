"""Execution handler for live trading.

Functionally identical to the backtest :class:`ExecutionHandler` — it still
emits ``OrderEvent`` objects onto the queue — but the engine registers the
live broker's ``handle_order`` as the ``ORDER`` consumer, so orders flow to a
real exchange instead of the simulated matcher. Kept as a separate class so
live-specific behaviour (broker-side position checks, order tagging, latency
logging) can be layered in without touching the backtest path.
"""
from __future__ import annotations

from ..backtest.execution_handler import ExecutionHandler
from ..core import SignalEvent
from ..utils import get_logger


class LiveExecutionHandler(ExecutionHandler):
    def __init__(self, *args, broker=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.broker = broker
        self.log = get_logger(self.__class__.__name__)

    def handle_signal(self, signal: SignalEvent) -> None:
        # In live mode the portfolio is kept in sync via FillEvents, so the
        # same sizing/risk logic applies. We just log more verbosely.
        prev_orders = self.orders_sent
        super().handle_signal(signal)
        if self.orders_sent > prev_orders:
            self.log.info(
                "LIVE order dispatched for %s (%s)", signal.symbol, signal.direction.name
            )
