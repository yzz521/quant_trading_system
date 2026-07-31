"""Strategy base class and context.

Subclasses implement :meth:`on_bar` (and optionally :meth:`on_init`,
:meth:`on_fill`). The base class maintains a per-symbol rolling bar buffer and
exposes a small set of helpers so most strategies need no direct access to the
engine internals.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd

from ..core import Bar, Direction, FillEvent, MarketEvent, SignalEvent
from ..utils import get_logger


class StrategyContext:
    """Read-only view a strategy gets of the outside world.

    Injected by the engine on registration. Strategies read positions through
    ``ctx.portfolio`` but never mutate it directly — orders only flow through
    signal events.
    """

    def __init__(self) -> None:
        self.portfolio = None  # set by engine
        self.engine = None     # set by engine


class Strategy:
    """Base class. ``symbols`` is the universe this strategy trades."""

    def __init__(self, symbols: list[str], name: str = "Strategy", **params) -> None:
        self.symbols = list(symbols)
        self.name = name
        self.params = params
        self.bars: dict[str, list[Bar]] = defaultdict(list)
        self.ctx = StrategyContext()
        self.log = get_logger(f"strategy.{name}")
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Engine wiring
    # ------------------------------------------------------------------ #
    def bind(self, engine, portfolio) -> None:
        self.ctx.engine = engine
        self.ctx.portfolio = portfolio

    @property
    def portfolio(self):
        return self.ctx.portfolio

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def emit_signal(self, symbol: str, direction: Direction, strength: float = 1.0) -> None:
        self.ctx.engine.put(SignalEvent(symbol=symbol, direction=direction, strength=strength))

    def close_all(self) -> None:
        if self.portfolio is None:
            return
        for sym, pos in self.portfolio.positions.items():
            if pos.quantity != 0:
                self.emit_signal(sym, Direction.EXIT)

    def to_series(self, symbol: str, field: str = "close", n: Optional[int] = None) -> pd.Series:
        """Return the rolling buffer for ``symbol`` as a pandas Series."""
        data = self.bars[symbol]
        if n is not None:
            data = data[-n:]
        if not data:
            return pd.Series(dtype=float)
        idx = pd.to_datetime([b.datetime for b in data])
        vals = [getattr(b, field) for b in data]
        return pd.Series(vals, index=idx, dtype=float)

    def position(self, symbol: str) -> float:
        if self.portfolio is None:
            return 0.0
        return self.portfolio.positions.get(symbol, _EmptyPos()).quantity

    # ------------------------------------------------------------------ #
    # Lifecycle hooks (override in subclasses)
    # ------------------------------------------------------------------ #
    def on_init(self) -> None:
        """Called once before the first bar."""
        self._initialized = True

    def on_bar(self, bar: Bar) -> None:
        """Called for every incoming bar. Override me."""
        raise NotImplementedError

    def on_fill(self, fill: FillEvent) -> None:
        """Called when an order for this strategy is filled."""

    # ------------------------------------------------------------------ #
    # Internal dispatch (called by the engine)
    # ------------------------------------------------------------------ #
    def handle_market(self, event: MarketEvent) -> None:
        if not self._initialized:
            self.on_init()
        if event.bar is None:
            return
        bar = event.bar
        if bar.symbol not in self.symbols:
            return
        self.bars[bar.symbol].append(bar)
        # Cap buffer to avoid runaway memory in long backtests.
        max_buf = int(self.params.get("max_buffer", 1000))
        if len(self.bars[bar.symbol]) > max_buf:
            self.bars[bar.symbol] = self.bars[bar.symbol][-max_buf:]
        self.on_bar(bar)


class _EmptyPos:
    """Sentinel for missing positions so callers can use ``.quantity`` safely."""

    quantity = 0.0
    avg_price = 0.0
