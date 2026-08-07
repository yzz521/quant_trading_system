"""Risk manager — the single choke-point every order passes through.

Adds:
- T+1: sell/exit quantity cannot exceed Portfolio.available(symbol)
- Projected single-name weight after the trade (portfolio-level concentration)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core import Direction, SignalEvent
from ..portfolio import Portfolio
from ..utils import get_logger


@dataclass
class RiskDecision:
    approved: bool
    adjusted_qty: float
    reason: str = ""


class RiskManager:
    def __init__(
        self,
        max_positions: int = 10,
        max_position_pct: float = 0.25,
        max_exposure: float = 1.0,
        max_drawdown: float = 0.20,
        min_cash_ratio: float = 0.05,
        lot_size: float = 1.0,
        enforce_t1: bool = True,
        max_orders_per_day: int = 0,  # 0 = unlimited
    ) -> None:
        self.max_positions = max_positions
        self.max_position_pct = max_position_pct
        self.max_exposure = max_exposure
        self.max_drawdown = max_drawdown
        self.min_cash_ratio = min_cash_ratio
        self.lot_size = lot_size
        self.enforce_t1 = enforce_t1
        self.max_orders_per_day = int(max_orders_per_day)
        self._orders_by_day: dict[str, int] = {}
        self._halted = False
        self.log = get_logger(self.__class__.__name__)

    def _current_drawdown(self, portfolio: Portfolio) -> float:
        curve = portfolio.equity_curve
        if len(curve) < 2:
            return 0.0
        peak = max(v for _, v in curve)
        if peak <= 0:
            return 0.0
        return (peak - portfolio.equity) / peak

    def _open_position_count(self, portfolio: Portfolio) -> int:
        return sum(1 for p in portfolio.positions.values() if p.is_open)

    def check(
        self,
        signal: SignalEvent,
        delta_qty: float,
        portfolio: Portfolio,
        price: float,
    ) -> RiskDecision:
        # --- T+1: never sell more than available (even on EXIT) ---
        if self.enforce_t1 and portfolio.t1_enabled and delta_qty < 0 and price > 0:
            avail = portfolio.available(signal.symbol)
            # delta_qty negative means sell long
            max_sell = avail
            if max_sell <= 0 and signal.direction in (Direction.EXIT, Direction.SHORT):
                return RiskDecision(False, 0.0, "t+1: no available shares to sell")
            if abs(delta_qty) > max_sell + 1e-9:
                delta_qty = -max_sell
                self.log.info(
                    "T+1 caps sell %s to available %.0f", signal.symbol, max_sell
                )
            if abs(delta_qty) < self.lot_size:
                return RiskDecision(False, 0.0, "t+1: available below lot size")

        # Order frequency limit (per calendar day, non-exit)
        if self.max_orders_per_day > 0 and signal.direction != Direction.EXIT:
            day_key = ""
            ts = getattr(signal, "timestamp", None)
            if ts is not None:
                day_key = str(ts.date() if hasattr(ts, "date") else ts)
            if not day_key:
                from datetime import date as _date
                day_key = str(_date.today())
            cnt = self._orders_by_day.get(day_key, 0)
            if cnt >= self.max_orders_per_day:
                return RiskDecision(False, 0.0, f"order frequency limit {self.max_orders_per_day}/day")
            self._orders_by_day[day_key] = cnt + 1

        # Exits (after T+1 trim) are allowed past drawdown halt
        if signal.direction == Direction.EXIT or (
            abs(delta_qty) < self.lot_size and delta_qty <= 0
        ):
            if abs(delta_qty) < self.lot_size:
                return RiskDecision(False, 0.0, "exit qty below lot size")
            return RiskDecision(True, delta_qty, "exit")

        if abs(delta_qty) < self.lot_size:
            return RiskDecision(True, delta_qty, "exit/small change")

        dd = self._current_drawdown(portfolio)
        if dd >= self.max_drawdown:
            self._halted = True
            self.log.warning(
                "Max drawdown breached (%.2f%%) — new entries halted", dd * 100
            )
            return RiskDecision(False, 0.0, f"max drawdown {dd:.2%}")

        # Single-name concentration on the *delta* notional
        if price > 0 and delta_qty != 0:
            notional = abs(delta_qty) * price
            if notional / max(portfolio.equity, 1.0) > self.max_position_pct:
                max_notional = portfolio.equity * self.max_position_pct
                delta_qty = (delta_qty / abs(delta_qty)) * (max_notional / price)
                self.log.info(
                    "Trimming %s to %.0f shares (position cap)",
                    signal.symbol,
                    abs(delta_qty),
                )

        # Projected position weight after trade (portfolio-level, all strategies share Portfolio)
        if price > 0 and portfolio.equity > 0 and delta_qty != 0:
            pos = portfolio.positions.get(signal.symbol)
            cur_qty = pos.quantity if pos else 0.0
            projected_qty = cur_qty + delta_qty
            projected_weight = abs(projected_qty * price) / portfolio.equity
            if projected_weight > self.max_position_pct + 1e-12:
                # Cap so final |qty| * price <= max_position_pct * equity
                max_qty = (portfolio.equity * self.max_position_pct) / price
                if projected_qty > 0:
                    allowed_delta = max_qty - cur_qty
                else:
                    allowed_delta = -max_qty - cur_qty
                if delta_qty > 0:
                    delta_qty = max(0.0, allowed_delta)
                else:
                    delta_qty = min(0.0, allowed_delta)
                delta_qty = (delta_qty // self.lot_size) * self.lot_size
                self.log.info(
                    "Projected weight cap trims %s delta to %.0f",
                    signal.symbol,
                    delta_qty,
                )
                if abs(delta_qty) < self.lot_size:
                    return RiskDecision(
                        False, 0.0, "projected position exceeds max_position_pct"
                    )

        pos = portfolio.positions.get(signal.symbol)
        is_new = pos is None or not pos.is_open
        if is_new and signal.direction in (Direction.LONG, Direction.SHORT):
            if self._open_position_count(portfolio) >= self.max_positions:
                return RiskDecision(False, 0.0, "max positions reached")

        if delta_qty > 0 and price > 0:
            cost = delta_qty * price
            min_cash = portfolio.equity * self.min_cash_ratio
            if portfolio.cash - cost < min_cash:
                affordable = max(0.0, portfolio.cash - min_cash)
                if affordable <= 0:
                    return RiskDecision(False, 0.0, "insufficient cash (floor)")
                delta_qty = (affordable / price) // self.lot_size * self.lot_size
                self.log.info("Cash floor caps %s to %.0f shares", signal.symbol, delta_qty)

        if price > 0 and portfolio.equity > 0:
            projected_gross = sum(
                abs(p.market_value) for p in portfolio.positions.values() if p.is_open
            ) + abs(delta_qty) * price
            max_gross = portfolio.equity * self.max_exposure
            if projected_gross > max_gross:
                allowed_extra = max(
                    0.0,
                    max_gross
                    - sum(
                        abs(p.market_value)
                        for p in portfolio.positions.values()
                        if p.is_open
                    ),
                )
                if delta_qty != 0:
                    delta_qty = (delta_qty / abs(delta_qty)) * (allowed_extra / price)
                    delta_qty = (delta_qty // self.lot_size) * self.lot_size
                self.log.info("Exposure cap trims %s to %.0f", signal.symbol, delta_qty)

        if abs(delta_qty) < self.lot_size:
            return RiskDecision(False, 0.0, "below lot size after risk trim")

        return RiskDecision(True, delta_qty, "ok")
