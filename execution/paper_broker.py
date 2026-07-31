"""Paper broker — real-time simulation, no real money, no credentials.

Reuses the same fill model as the backtest broker but operates on the
*current* bar (live fills happen at the next tick, so using the incoming bar
is a fair approximation). Combined with a polling :class:`LiveFeed` this lets
you validate a strategy end-to-end before connecting a real account.
"""
from __future__ import annotations

from uuid import uuid4

from ..core import Direction, FillEvent, MarketEvent, OrderEvent
from ..utils import get_logger, safe_round
from .broker_base import LiveBroker, LiveBrokerConfig


class PaperBroker(LiveBroker):
    name = "paper"

    def __init__(self, config: LiveBrokerConfig | None = None,
                 commission_rate: float = 0.0003,
                 slippage_bps: float = 2.0,
                 initial_cash: float = 1_000_000.0) -> None:
        super().__init__(config or LiveBrokerConfig(paper=True))
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps
        self.cash = initial_cash
        self.positions: dict[str, float] = {}
        self.avg_prices: dict[str, float] = {}
        self._last_prices: dict[str, float] = {}
        self._pending: list[OrderEvent] = []
        self.log = get_logger(self.__class__.__name__)

    # -- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        self._connected = True
        self.log.info("PaperBroker connected (cash=%.2f)", self.cash)

    def disconnect(self) -> None:
        self._connected = False
        self.log.info("PaperBroker disconnected")

    # -- orders ----------------------------------------------------------
    def place_order(self, order: OrderEvent) -> str:
        broker_id = str(uuid4())[:8]
        order.order_id = broker_id
        self._pending.append(order)
        return broker_id

    def cancel_order(self, broker_order_id: str) -> None:
        self._pending = [o for o in self._pending if o.order_id != broker_order_id]

    def get_position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def get_cash(self) -> float:
        return self.cash

    # -- market data hook (register as MARKET handler) ------------------
    def handle_market(self, event: MarketEvent) -> None:
        if event.bar is None:
            return
        bar = event.bar
        self._last_prices[bar.symbol] = bar.close
        # Fill pending orders for this symbol at the current bar close.
        remaining: list[OrderEvent] = []
        for order in self._pending:
            if order.symbol == bar.symbol:
                self._fill(order, bar.close, bar.datetime)
            else:
                remaining.append(order)
        self._pending = remaining

    def _fill(self, order: OrderEvent, price: float, dt) -> None:
        slip = self.slippage_bps / 10_000.0
        fill_price = price * (1 + slip) if order.direction == Direction.LONG else price * (1 - slip)
        qty = order.quantity
        signed = qty if order.direction == Direction.LONG else -qty

        # Update position + cash
        cur = self.positions.get(order.symbol, 0.0)
        new_qty = cur + signed
        self.positions[order.symbol] = new_qty
        gross = qty * fill_price
        comm = gross * self.commission_rate
        if signed > 0:
            self.cash -= gross + comm
        else:
            self.cash += gross - comm

        fill = FillEvent(
            symbol=order.symbol,
            direction=order.direction,
            quantity=qty,
            fill_price=safe_round(fill_price, 4),
            commission=safe_round(comm, 2),
            order_id=order.order_id,
            fill_id=str(uuid4())[:8],
            timestamp=dt,
        )
        self.log.info("PAPER FILL %s %s %d @ %.4f", order.symbol,
                      order.direction.name, qty, fill_price)
        if self._engine is not None:
            self._engine.put(fill)
