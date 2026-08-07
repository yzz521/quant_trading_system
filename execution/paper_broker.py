"""Paper broker — real-time simulation, no real money, no credentials.

Enhancements (phase 6):
- Order status tracking (pending / filled / cancelled)
- Idempotent place_order by order_id
- ``reconcile()`` snapshot for cash/positions vs Portfolio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4

from ..core import Direction, FillEvent, MarketEvent, OrderEvent
from ..utils import get_logger, safe_round
from .broker_base import LiveBroker, LiveBrokerConfig


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class TrackedOrder:
    order: OrderEvent
    status: OrderStatus = OrderStatus.PENDING
    fill_id: Optional[str] = None


class PaperBroker(LiveBroker):
    name = "paper"

    def __init__(
        self,
        config: LiveBrokerConfig | None = None,
        commission_rate: float = 0.0003,
        slippage_bps: float = 2.0,
        initial_cash: float = 1_000_000.0,
    ) -> None:
        super().__init__(config or LiveBrokerConfig(paper=True))
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps
        self.cash = initial_cash
        self.positions: dict[str, float] = {}
        self.avg_prices: dict[str, float] = {}
        self._last_prices: dict[str, float] = {}
        self._orders: dict[str, TrackedOrder] = {}
        self._engine = None
        self.log = get_logger(self.__class__.__name__)

    def set_engine(self, engine) -> None:
        self._engine = engine

    def connect(self) -> None:
        self._connected = True
        self.log.info("PaperBroker connected (cash=%.2f)", self.cash)

    def disconnect(self) -> None:
        self._connected = False
        self.log.info("PaperBroker disconnected")

    def place_order(self, order: OrderEvent) -> str:
        if order.order_id and order.order_id in self._orders:
            existing = self._orders[order.order_id]
            if existing.status == OrderStatus.PENDING:
                self.log.warning("Idempotent skip duplicate order_id=%s", order.order_id)
                return order.order_id
        broker_id = order.order_id or str(uuid4())[:8]
        order.order_id = broker_id
        self._orders[broker_id] = TrackedOrder(order=order)
        return broker_id

    def cancel_order(self, broker_order_id: str) -> None:
        tracked = self._orders.get(broker_order_id)
        if tracked and tracked.status == OrderStatus.PENDING:
            tracked.status = OrderStatus.CANCELLED

    def get_position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def get_cash(self) -> float:
        return self.cash

    def pending_orders(self) -> list[OrderEvent]:
        return [t.order for t in self._orders.values() if t.status == OrderStatus.PENDING]

    def handle_market(self, event: MarketEvent) -> None:
        if event.bar is None:
            return
        bar = event.bar
        self._last_prices[bar.symbol] = bar.close
        for oid, tracked in list(self._orders.items()):
            if tracked.status != OrderStatus.PENDING:
                continue
            if tracked.order.symbol == bar.symbol:
                self._fill(tracked, bar.close, bar.datetime)

    def _fill(self, tracked: TrackedOrder, price: float, dt) -> None:
        order = tracked.order
        slip = self.slippage_bps / 10_000.0
        fill_price = (
            price * (1 + slip) if order.direction == Direction.LONG else price * (1 - slip)
        )
        qty = order.quantity
        signed = qty if order.direction == Direction.LONG else -qty

        cur = self.positions.get(order.symbol, 0.0)
        new_qty = cur + signed
        self.positions[order.symbol] = new_qty
        gross = qty * fill_price
        comm = gross * self.commission_rate
        if signed > 0:
            self.cash -= gross + comm
            # avg price
            if cur <= 0:
                self.avg_prices[order.symbol] = fill_price
            else:
                self.avg_prices[order.symbol] = (
                    self.avg_prices.get(order.symbol, fill_price) * cur + fill_price * qty
                ) / new_qty
        else:
            self.cash += gross - comm

        fill_id = str(uuid4())[:8]
        tracked.status = OrderStatus.FILLED
        tracked.fill_id = fill_id

        fill = FillEvent(
            symbol=order.symbol,
            direction=order.direction,
            quantity=qty,
            fill_price=safe_round(fill_price, 4),
            commission=safe_round(comm, 2),
            order_id=order.order_id,
            fill_id=fill_id,
            timestamp=dt,
        )
        self.log.info(
            "PAPER FILL %s %s %d @ %.4f",
            order.symbol,
            order.direction.name,
            qty,
            fill_price,
        )
        if self._engine is not None:
            self._engine.put(fill)

    def flush_pending(self) -> int:
        """Fill remaining pending orders at last known prices (session end)."""
        n = 0
        for oid, tracked in list(self._orders.items()):
            if tracked.status != OrderStatus.PENDING:
                continue
            sym = tracked.order.symbol
            px = self._last_prices.get(sym)
            if px is None or px <= 0:
                continue
            self._fill(tracked, px, tracked.order.timestamp)
            n += 1
        return n

    def reconcile(self, portfolio=None) -> dict:
        """Return a snapshot; if ``portfolio`` given, report cash/position diffs."""
        snap = {
            "cash": self.cash,
            "positions": dict(self.positions),
            "pending": len(self.pending_orders()),
            "last_prices": dict(self._last_prices),
        }
        if portfolio is not None:
            cash_diff = abs(self.cash - portfolio.cash)
            pos_diff = {}
            symbols = set(self.positions) | set(portfolio.positions)
            for s in symbols:
                bq = self.positions.get(s, 0.0)
                pq = portfolio.positions[s].quantity if s in portfolio.positions else 0.0
                if abs(bq - pq) > 1e-6:
                    pos_diff[s] = {"broker": bq, "portfolio": pq}
            snap["cash_diff"] = cash_diff
            snap["position_diffs"] = pos_diff
            snap["ok"] = cash_diff < 1.0 and not pos_diff
        return snap
