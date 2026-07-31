"""Portfolio: tracks cash, positions, and the equity curve.

The portfolio is the single source of truth for "what do I own right now".
It reacts to two event types:

* ``MARKET`` — mark every open position to the latest bar close and append a
  point to the equity curve.
* ``FILL``   — update cash, position quantity and average cost on execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from ..core import Bar, Direction, FillEvent, MarketEvent
from ..utils import get_logger, safe_round


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0          # positive long, negative short
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        if self.quantity == 0:
            return 0.0
        return (self.last_price - self.avg_price) * self.quantity

    @property
    def is_open(self) -> bool:
        return abs(self.quantity) > 1e-9


class Portfolio:
    """Cash + positions + equity history."""

    def __init__(self, initial_capital: float = 1_000_000.0, currency: str = "CNY") -> None:
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.currency = currency
        self.positions: dict[str, Position] = {}
        self.equity_curve: list[tuple[datetime, float]] = []
        self.fills: list[FillEvent] = []
        self.trades: list[dict] = []
        self._last_dt: Optional[datetime] = None
        self.log = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    def _get_or_create(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #
    def on_market(self, event: MarketEvent) -> None:
        if event.bar is None:
            return
        bar = event.bar
        pos = self._get_or_create(bar.symbol)
        pos.last_price = bar.close
        self._last_dt = bar.datetime
        # Record equity once per bar (after all symbols updated is handled by
        # the engine calling snapshot() at end of timestamp).

    def snapshot(self, dt: Optional[datetime] = None) -> float:
        """Append current equity to the curve and return it."""
        ts = dt or self._last_dt or datetime.now()
        equity = self.equity
        self.equity_curve.append((ts, equity))
        return equity

    def on_fill(self, event: FillEvent) -> None:
        self.fills.append(event)
        pos = self._get_or_create(event.symbol)
        signed_qty = event.quantity if event.direction == Direction.LONG else -event.quantity
        # EXIT orders carry direction as the side that closes the position;
        # the broker sets event.quantity positive and direction to LONG/SHORT
        # of the closing trade. We use signed_qty consistently.
        new_qty = pos.quantity + signed_qty

        # Realized PnL when reducing/closing a position
        if pos.quantity != 0 and ((signed_qty < 0 and pos.quantity > 0) or
                                  (signed_qty > 0 and pos.quantity < 0)):
            closing_qty = min(abs(signed_qty), abs(pos.quantity))
            if pos.quantity > 0:
                realized = (event.fill_price - pos.avg_price) * closing_qty
            else:
                realized = (pos.avg_price - event.fill_price) * closing_qty
            pos.realized_pnl += realized
            self.cash += (event.fill_price * closing_qty) - event.commission
            self.trades.append({
                "symbol": event.symbol,
                "side": "close_long" if pos.quantity > 0 else "close_short",
                "quantity": closing_qty,
                "entry_price": pos.avg_price,
                "exit_price": event.fill_price,
                "pnl": realized,
                "exit_time": event.timestamp,
            })
        else:
            # Opening or increasing — cash outlay/inflow
            if signed_qty > 0:
                self.cash -= signed_qty * event.fill_price + event.commission
            else:
                self.cash += -signed_qty * event.fill_price - event.commission

        # Update average cost on increases in the same direction
        if new_qty == 0:
            pos.avg_price = 0.0
        elif (pos.quantity == 0) or (pos.quantity > 0 and signed_qty > 0) or \
             (pos.quantity < 0 and signed_qty < 0):
            pos.avg_price = (
                (pos.avg_price * pos.quantity + event.fill_price * signed_qty) / new_qty
                if new_qty != 0 else 0.0
            )
        pos.quantity = new_qty
        pos.last_price = event.fill_price

    # ------------------------------------------------------------------ #
    @property
    def equity(self) -> float:
        mv = sum(p.market_value for p in self.positions.values())
        return safe_round(self.cash + mv)

    @property
    def exposure(self) -> float:
        """Gross exposure as a fraction of equity."""
        eq = self.equity
        if eq <= 0:
            return 0.0
        gross = sum(abs(p.market_value) for p in self.positions.values())
        return gross / eq

    def equity_curve_frame(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame(columns=["datetime", "equity"]).set_index("datetime")
        df = pd.DataFrame(self.equity_curve, columns=["datetime", "equity"])
        df = df.set_index("datetime")
        df["return"] = df["equity"].pct_change().fillna(0.0)
        return df

    def positions_frame(self) -> pd.DataFrame:
        rows = []
        for sym, p in self.positions.items():
            if p.is_open:
                rows.append({
                    "symbol": sym,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "last_price": p.last_price,
                    "market_value": p.market_value,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                })
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).set_index("symbol")

    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())
