"""Execution handler — converts signals into risk-checked orders.

Sits between strategies and the broker. For every :class:`SignalEvent` it:

1. Asks the position sizer for a target quantity.
2. Computes the delta vs the current position.
3. Runs the risk manager (T+1 + concentration + exposure), which may trim or reject.
4. Emits an :class:`OrderEvent` for the surviving quantity.
"""
from __future__ import annotations

from uuid import uuid4

from ..core import Direction, OrderEvent, OrderType, SignalEvent
from ..portfolio import Portfolio, PositionSizer
from ..risk import RiskManager
from ..utils import get_logger


class ExecutionHandler:
    def __init__(
        self,
        engine,
        portfolio: Portfolio,
        sizer: PositionSizer,
        risk_manager: RiskManager,
        lot_size: float = 1.0,
    ) -> None:
        self.engine = engine
        self.portfolio = portfolio
        self.sizer = sizer
        self.risk_manager = risk_manager
        self.lot_size = lot_size
        self.log = get_logger(self.__class__.__name__)
        self.orders_sent = 0

    def handle_signal(self, signal: SignalEvent) -> None:
        pos = self.portfolio.positions.get(signal.symbol)
        current_qty = pos.quantity if pos else 0.0
        last_price = pos.last_price if pos else 0.0
        if last_price <= 0:
            return

        if signal.direction == Direction.EXIT:
            # Flatten using available qty under T+1 when enabled
            if self.portfolio.t1_enabled:
                avail = self.portfolio.available(signal.symbol)
                target_qty = current_qty - avail if current_qty > 0 else 0.0
                # For long: sell only available -> target = frozen part left
                if current_qty > 0:
                    target_qty = current_qty - avail
                else:
                    target_qty = 0.0
            else:
                target_qty = 0.0
        else:
            target_qty = self.sizer.target_quantity(signal, self.portfolio, last_price)

        delta = target_qty - current_qty

        decision = self.risk_manager.check(signal, delta, self.portfolio, last_price)
        if not decision.approved:
            self.log.debug(
                "Rejected %s %s: %s",
                signal.symbol,
                signal.direction.name,
                decision.reason,
            )
            return

        delta = decision.adjusted_qty
        if abs(delta) < self.lot_size:
            return

        direction = Direction.LONG if delta > 0 else Direction.SHORT
        order = OrderEvent(
            symbol=signal.symbol,
            direction=direction,
            quantity=abs(delta),
            order_type=OrderType.MARKET,
            order_id=str(uuid4())[:8],
            timestamp=signal.timestamp,
        )
        self.orders_sent += 1
        self.engine.put(order)
