"""Portfolio: tracks cash, positions, and the equity curve.

Supports optional A-share style T+1: shares bought today are ``frozen`` and
cannot be sold until the next session date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd

from ..core import Direction, FillEvent, MarketEvent
from ..utils import get_logger, safe_round


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0          # positive long, negative short
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0
    frozen_quantity: float = 0.0   # long shares locked by T+1 (bought today)

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

    @property
    def available_quantity(self) -> float:
        """Shares that can be sold under T+1 (long side only)."""
        if self.quantity <= 0:
            return self.quantity  # short or flat: no long freeze concept
        return max(0.0, self.quantity - self.frozen_quantity)


class Portfolio:
    """Cash + positions + equity history."""

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        currency: str = "CNY",
        t1_enabled: bool = True,
    ) -> None:
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.currency = currency
        self.t1_enabled = bool(t1_enabled)
        self.positions: dict[str, Position] = {}
        self.equity_curve: list[tuple[datetime, float]] = []
        self.fills: list[FillEvent] = []
        self.trades: list[dict] = []
        self._last_dt: Optional[datetime] = None
        self._session_date: Optional[date] = None
        self.log = get_logger(self.__class__.__name__)

    def _get_or_create(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def _roll_session(self, dt: datetime) -> None:
        """On a new calendar/session date, unfreeze yesterday's buys (T+1)."""
        d = dt.date() if isinstance(dt, datetime) else dt
        if self._session_date is None:
            self._session_date = d
            return
        if d > self._session_date:
            for pos in self.positions.values():
                if pos.frozen_quantity:
                    self.log.debug(
                        "T+1 unfreeze %s: frozen %.0f -> 0", pos.symbol, pos.frozen_quantity
                    )
                    pos.frozen_quantity = 0.0
            self._session_date = d

    def on_market(self, event: MarketEvent) -> None:
        if event.bar is None:
            return
        bar = event.bar
        if self.t1_enabled:
            self._roll_session(bar.datetime)
        pos = self._get_or_create(bar.symbol)
        pos.last_price = bar.close
        self._last_dt = bar.datetime
        # Equity is recorded via snapshot() at end of timestamp by the engine.

    def snapshot(self, dt: Optional[datetime] = None) -> float:
        """Append current equity to the curve and return it."""
        ts = dt or self._last_dt or datetime.now()
        if self.t1_enabled:
            self._roll_session(ts)
        equity = self.equity
        self.equity_curve.append((ts, equity))
        return equity

    def on_fill(self, event: FillEvent) -> None:
        self.fills.append(event)
        pos = self._get_or_create(event.symbol)
        if self.t1_enabled and event.timestamp:
            self._roll_session(event.timestamp)

        signed_qty = event.quantity if event.direction == Direction.LONG else -event.quantity
        new_qty = pos.quantity + signed_qty

        # Realized PnL when reducing/closing a position
        if pos.quantity != 0 and (
            (signed_qty < 0 and pos.quantity > 0) or (signed_qty > 0 and pos.quantity < 0)
        ):
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
            # Selling long does not increase frozen; frozen only tracks unsettled buys.
            # available shrinks via quantity decrease; frozen stays until day roll
            # unless quantity falls below frozen (clamp).
        else:
            if signed_qty > 0:
                self.cash -= signed_qty * event.fill_price + event.commission
                if self.t1_enabled:
                    pos.frozen_quantity += signed_qty
            else:
                self.cash += -signed_qty * event.fill_price - event.commission

        if new_qty == 0:
            pos.avg_price = 0.0
            pos.frozen_quantity = 0.0
        elif (pos.quantity == 0) or (pos.quantity > 0 and signed_qty > 0) or (
            pos.quantity < 0 and signed_qty < 0
        ):
            pos.avg_price = (
                (pos.avg_price * pos.quantity + event.fill_price * signed_qty) / new_qty
                if new_qty != 0
                else 0.0
            )
        pos.quantity = new_qty
        pos.last_price = event.fill_price
        if pos.quantity > 0:
            pos.frozen_quantity = min(pos.frozen_quantity, pos.quantity)
        else:
            pos.frozen_quantity = 0.0

    @property
    def equity(self) -> float:
        mv = sum(p.market_value for p in self.positions.values())
        return safe_round(self.cash + mv)

    @property
    def exposure(self) -> float:
        eq = self.equity
        if eq <= 0:
            return 0.0
        gross = sum(abs(p.market_value) for p in self.positions.values())
        return gross / eq

    def available(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        if not self.t1_enabled:
            return pos.quantity
        return pos.available_quantity

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
                    "available": p.available_quantity if self.t1_enabled else p.quantity,
                    "frozen": p.frozen_quantity,
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
