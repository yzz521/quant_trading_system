"""Risk manager — the single choke-point every order passes through.

A signal becomes an order only after the risk manager signs off. The checks
here are deliberately conservative and self-contained; in a production system
you would add real-time Greek limits, correlation breaks and fat-finger
guards, but the structure stays the same.

Decision flow::

    ExecutionHandler -> RiskManager.check -> (approved? OrderEvent : reject log)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
        max_position_pct: float = 0.25,   # max % equity in one name
        max_exposure: float = 1.0,        # gross exposure / equity
        max_drawdown: float = 0.20,       # halt new entries beyond this DD
        min_cash_ratio: float = 0.05,     # keep at least 5% cash
        lot_size: float = 1.0,
    ) -> None:
        self.max_positions = max_positions
        self.max_position_pct = max_position_pct
        self.max_exposure = max_exposure
        self.max_drawdown = max_drawdown
        self.min_cash_ratio = min_cash_ratio
        self.lot_size = lot_size
        self._halted = False
        self.log = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    def check(
        self,
        signal: SignalEvent,
        delta_qty: float,             # signed desired change in quantity
        portfolio: Portfolio,
        price: float,
    ) -> RiskDecision:
        # Exits are always allowed — risk management should never trap a
        # position it can't close.
        if signal.direction == Direction.EXIT or abs(delta_qty) < self.lot_size:
            return RiskDecision(True, delta_qty, "exit/small change")

        # Drawdown circuit breaker
        dd = self._current_drawdown(portfolio)
        if dd >= self.max_drawdown:
            self._halted = True
            self.log.warning("Max drawdown breached (%.2f%%) — new entries halted", dd * 100)
            return RiskDecision(False, 0.0, f"max drawdown {dd:.2%}")

        # Single-name concentration
        if price > 0:
            notional = abs(delta_qty) * price
            if notional / max(portfolio.equity, 1.0) > self.max_position_pct:
                # Cap the delta to the max position pct
                max_notional = portfolio.equity * self.max_position_pct
                delta_qty = (delta_qty / abs(delta_qty)) * (max_notional / price)
                self.log.info("Trimming %s to %.0f shares (position cap)",
                              signal.symbol, abs(delta_qty))

        # Position count (only counts as a new position if currently flat)
        pos = portfolio.positions.get(signal.symbol)
        is_new = pos is None or not pos.is_open
        if is_new and signal.direction in (Direction.LONG, Direction.SHORT):
            if self._open_position_count(portfolio) >= self.max_positions:
                return RiskDecision(False, 0.0, "max positions reached")

        # Cash floor — only blocks longs that need more cash
        if delta_qty > 0 and price > 0:
            cost = delta_qty * price
            min_cash = portfolio.equity * self.min_cash_ratio
            if portfolio.cash - cost < min_cash:
                affordable = max(0.0, portfolio.cash - min_cash)
                if affordable <= 0:
                    return RiskDecision(False, 0.0, "insufficient cash (floor)")
                delta_qty = (affordable / price) // self.lot_size * self.lot_size
                self.log.info("Cash floor caps %s to %.0f shares", signal.symbol, delta_qty)

        # Gross exposure cap
        if price > 0 and portfolio.equity > 0:
            projected_gross = sum(
                abs(p.market_value) for p in portfolio.positions.values() if p.is_open
            ) + abs(delta_qty) * price
            max_gross = portfolio.equity * self.max_exposure
            if projected_gross > max_gross:
                allowed_extra = max(0.0, max_gross - sum(
                    abs(p.market_value) for p in portfolio.positions.values() if p.is_open))
                delta_qty = (delta_qty / abs(delta_qty)) * (allowed_extra / price)
                delta_qty = (delta_qty // self.lot_size) * self.lot_size
                self.log.info("Exposure cap trims %s to %.0f", signal.symbol, delta_qty)

        if abs(delta_qty) < self.lot_size:
            return RiskDecision(False, 0.0, "below lot size after risk trim")

        return RiskDecision(True, delta_qty, "ok")
