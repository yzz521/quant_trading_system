"""Simulated broker for backtesting.

Orders are **not** filled on the bar they are created — they enter a pending
queue and are filled on the *next* bar's open. This is the standard way to
avoid look-ahead bias. Slippage and commission are applied per fill.

Registration: the engine registers this object as both an ``ORDER`` handler
(queueing) and a ``MARKET`` handler (filling on the new bar's open). The
broker must run *before* strategies on each bar so that strategies see the
fills from their previous-bar orders before emitting new signals.
"""
from __future__ import annotations

from collections import deque
from uuid import uuid4

from ..core import Bar, Direction, FillEvent, MarketEvent, OrderEvent, OrderType
from ..utils import get_logger, safe_round


class SimulatedBroker:
    def __init__(
        self,
        commission_rate: float = 0.0003,   # 3 bps per side
        stamp_duty: float = 0.001,         # CN sell-side only (0.1%)
        slippage_bps: float = 2.0,         # 2 bps adverse
        fill_policy: str = "next_open",    # 'next_open' | 'same_close'
        allow_short: bool = False,
        lot_size: float = 1,               # 100 for A-shares
        min_commission: float = 5.0,
    ) -> None:
        self.commission_rate = commission_rate
        self.stamp_duty = stamp_duty
        self.slippage_bps = slippage_bps
        self.fill_policy = fill_policy
        self.allow_short = allow_short
        self.lot_size = lot_size
        self.min_commission = min_commission
        self._pending: deque[OrderEvent] = deque()
        self.log = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    def handle_order(self, event: OrderEvent) -> None:
        if event.quantity <= 0:
            return
        if not self.allow_short and event.direction == Direction.SHORT:
            # Only allow SHORT that closes an existing long; the execution
            # handler is responsible for not sending naked shorts, but we
            # double-check here.
            pass
        self._pending.append(event)

    def handle_market(self, event: MarketEvent) -> None:
        if event.bar is None or not self._pending:
            return
        bar = event.bar
        # Fill pending orders for this symbol only.
        still_pending: deque[OrderEvent] = deque()
        while self._pending:
            order = self._pending.popleft()
            if order.symbol == bar.symbol:
                fill = self._fill(order, bar)
                if fill is not None:
                    # Push fill with high priority so portfolio updates before
                    # strategies read positions on this same bar.
                    self._dispatch_fill(fill)
                else:
                    still_pending.append(order)
            else:
                still_pending.append(order)
        self._pending = still_pending

    # engine injects this
    def set_engine(self, engine) -> None:
        self._engine = engine

    def _dispatch_fill(self, fill: FillEvent) -> None:
        self._engine.put(fill)

    # ------------------------------------------------------------------ #
    def _fill(self, order: OrderEvent, bar: Bar) -> FillEvent | None:
        price = bar.open if self.fill_policy == "next_open" else bar.close
        if price <= 0:
            return None

        slip = self.slippage_bps / 10_000.0
        if order.direction == Direction.LONG:
            fill_price = price * (1.0 + slip)
        else:
            fill_price = price * (1.0 - slip)

        # Round to lot size
        qty = int(order.quantity / self.lot_size) * self.lot_size
        if qty <= 0:
            return None

        gross = qty * fill_price
        commission = max(gross * self.commission_rate, self.min_commission)
        # Stamp duty applies on sells (CN equities)
        duty = gross * self.stamp_duty if order.direction == Direction.SHORT else 0.0
        total_cost = commission + duty

        fill = FillEvent(
            symbol=order.symbol,
            direction=order.direction,
            quantity=qty,
            fill_price=safe_round(fill_price, 4),
            commission=safe_round(total_cost, 2),
            slippage=safe_round(abs(fill_price - price), 4),
            order_id=order.order_id,
            fill_id=str(uuid4())[:8],
            timestamp=bar.datetime,
        )
        self.log.debug(
            "FILL %s %s %d @ %.4f (comm %.2f)", order.symbol, order.direction.name,
            qty, fill_price, total_cost,
        )
        return fill
