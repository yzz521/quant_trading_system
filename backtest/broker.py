"""Simulated broker for backtesting.

Orders are filled on the *next* bar's open by default (no look-ahead).
Optional A-share style constraints:

- **涨跌停**: buy cannot fill when open is at/above limit-up; sell cannot fill
  at/below limit-down (based on previous close tracked by the broker).
- **成交量约束**: fill quantity capped to ``max_volume_pct * bar.volume``.
"""
from __future__ import annotations

from collections import deque
from uuid import uuid4

from ..core import Bar, Direction, FillEvent, MarketEvent, OrderEvent
from ..utils import get_logger, safe_round


class SimulatedBroker:
    def __init__(
        self,
        commission_rate: float = 0.0003,
        stamp_duty: float = 0.001,
        slippage_bps: float = 2.0,
        fill_policy: str = "next_open",
        allow_short: bool = False,
        lot_size: float = 1,
        min_commission: float = 5.0,
        # --- market realism ---
        limit_pct: float = 0.10,
        enforce_limit: bool = True,
        max_volume_pct: float = 0.25,
        enforce_volume: bool = True,
        skip_zero_volume: bool = True,
    ) -> None:
        self.commission_rate = commission_rate
        self.stamp_duty = stamp_duty
        self.slippage_bps = slippage_bps
        self.fill_policy = fill_policy
        self.allow_short = allow_short
        self.lot_size = lot_size
        self.min_commission = min_commission
        self.limit_pct = limit_pct
        self.enforce_limit = enforce_limit
        self.max_volume_pct = max_volume_pct
        self.enforce_volume = enforce_volume
        self.skip_zero_volume = skip_zero_volume
        self._pending: deque[OrderEvent] = deque()
        self._prev_close: dict[str, float] = {}
        self._engine = None
        self.rejected_orders = 0
        self.log = get_logger(self.__class__.__name__)

    def set_engine(self, engine) -> None:
        self._engine = engine

    def handle_order(self, event: OrderEvent) -> None:
        if event.quantity <= 0:
            return
        self._pending.append(event)


    def handle_market(self, event: MarketEvent) -> None:
        if event.bar is None:
            return
        bar = event.bar
        still_pending: deque[OrderEvent] = deque()
        while self._pending:
            order = self._pending.popleft()
            if order.symbol != bar.symbol:
                still_pending.append(order)
                continue
            # 停牌：volume<=0，订单继续挂起
            if self.skip_zero_volume and float(getattr(bar, "volume", 0) or 0) <= 0:
                still_pending.append(order)
                self.log.info("Defer %s: zero volume (suspended?)", order.symbol)
                continue
            fill = self._fill(order, bar)
            if fill is not None:
                self._dispatch_fill(fill)
            # else rejected (limit etc.) — drop
        self._pending = still_pending
        if bar.close > 0:
            self._prev_close[bar.symbol] = float(bar.close)


    def _dispatch_fill(self, fill: FillEvent) -> None:
        if self._engine is not None:
            self._engine.put(fill)

    def _at_limit_up(self, bar: Bar) -> bool:
        prev = self._prev_close.get(bar.symbol)
        if prev is None or prev <= 0:
            return False
        limit_up = prev * (1.0 + self.limit_pct)
        # open locked at limit-up (tolerance 0.1%)
        return bar.open >= limit_up * 0.999

    def _at_limit_down(self, bar: Bar) -> bool:
        prev = self._prev_close.get(bar.symbol)
        if prev is None or prev <= 0:
            return False
        limit_down = prev * (1.0 - self.limit_pct)
        return bar.open <= limit_down * 1.001

    def _fill(self, order: OrderEvent, bar: Bar) -> FillEvent | None:
        price = bar.open if self.fill_policy == "next_open" else bar.close
        if price <= 0:
            return None

        # --- 涨跌停 ---
        if self.enforce_limit:
            if order.direction == Direction.LONG and self._at_limit_up(bar):
                self.rejected_orders += 1
                self.log.info(
                    "Reject BUY %s: limit-up (open=%.4f)", order.symbol, bar.open
                )
                return None
            if order.direction == Direction.SHORT and self._at_limit_down(bar):
                self.rejected_orders += 1
                self.log.info(
                    "Reject SELL %s: limit-down (open=%.4f)", order.symbol, bar.open
                )
                return None

        qty = int(order.quantity / self.lot_size) * self.lot_size
        if qty <= 0:
            return None

        # --- 成交量约束 ---
        if self.enforce_volume and bar.volume > 0 and self.max_volume_pct > 0:
            max_qty = int(bar.volume * self.max_volume_pct / self.lot_size) * self.lot_size
            if max_qty <= 0:
                self.rejected_orders += 1
                self.log.info("Reject %s: volume cap zero", order.symbol)
                return None
            if qty > max_qty:
                self.log.info(
                    "Volume cap %s: %.0f -> %.0f", order.symbol, qty, max_qty
                )
                qty = max_qty

        slip = self.slippage_bps / 10_000.0
        if order.direction == Direction.LONG:
            fill_price = price * (1.0 + slip)
        else:
            fill_price = price * (1.0 - slip)

        gross = qty * fill_price
        commission = max(gross * self.commission_rate, self.min_commission)
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
            "FILL %s %s %d @ %.4f (comm %.2f)",
            order.symbol,
            order.direction.name,
            qty,
            fill_price,
            total_cost,
        )
        return fill
