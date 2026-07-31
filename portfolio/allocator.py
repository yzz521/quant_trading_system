"""Position sizers — turn a signal into a target quantity.

A sizer answers: "given this signal and the current portfolio, what is the
*target* quantity for this symbol?" The execution handler then computes the
delta vs the current position and emits an order. Keeping sizing out of the
strategy makes it trivial to switch from equal-weight to risk-parity without
touching strategy code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core import Direction, SignalEvent
from .manager import Portfolio


class PositionSizer(ABC):
    @abstractmethod
    def target_quantity(self, signal: SignalEvent, portfolio: Portfolio,
                        last_price: float) -> float:
        """Return the desired signed quantity (0 = flat)."""
        raise NotImplementedError


class EqualWeightSizer(PositionSizer):
    """Allocate a fixed fraction of equity per name.

    ``weight`` is the fraction of equity per position (e.g. 0.1 = 10%).
    Signal ``strength`` scales the weight so a half-confidence signal gets
    half the size.
    """

    def __init__(self, weight: float = 0.1, max_weight: float = 1.0) -> None:
        self.weight = weight
        self.max_weight = max_weight

    def target_quantity(self, signal, portfolio, last_price):
        if last_price <= 0:
            return 0.0
        equity = portfolio.equity
        w = min(self.weight * abs(signal.strength), self.max_weight)
        target_value = equity * w
        if signal.direction == Direction.LONG:
            return target_value / last_price
        if signal.direction == Direction.SHORT:
            return -target_value / last_price
        # EXIT
        return 0.0


class VolTargetSizer(PositionSizer):
    """Size each position so it contributes a target annualized volatility.

    Uses the signal's ``strength`` as a rough inverse-vol proxy when no
    volatility estimate is supplied. ``target_vol`` is annualized (e.g. 0.15).
    """

    def __init__(self, target_vol: float = 0.15, annualization: float = 252) -> None:
        self.target_vol = target_vol
        self.annualization = annualization

    def target_quantity(self, signal, portfolio, last_price):
        if last_price <= 0:
            return 0.0
        equity = portfolio.equity
        # Without a vol estimate we fall back to a fixed notional consistent
        # with target_vol assuming 20% vol — replace with real vol in prod.
        assumed_vol = max(0.05, 0.20 * abs(signal.strength))
        notional = equity * (self.target_vol / assumed_vol)
        notional = min(notional, equity)  # never more than 100% per name
        qty = notional / last_price
        if signal.direction == Direction.SHORT:
            qty = -qty
        if signal.direction == Direction.EXIT:
            qty = 0.0
        return qty
