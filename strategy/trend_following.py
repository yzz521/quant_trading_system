"""Trend-following strategies.

* :class:`MovingAverageCrossStrategy` — classic short/long MA crossover.
* :class:`TurtleBreakoutStrategy` — Donchian-channel breakout (turtle trading).
"""
from __future__ import annotations

import numpy as np

from ..core import Bar, Direction
from .base import Strategy


class MovingAverageCrossStrategy(Strategy):
    """Go long when the fast MA crosses above the slow MA; exit (or short)
    on the opposite cross.

    Params
    ------
    fast : int       fast MA window (default 5)
    slow : int       slow MA window (default 20)
    allow_short : bool   short on bearish cross (default False)
    """

    def __init__(self, symbols, fast: int = 5, slow: int = 20,
                 allow_short: bool = False, name: str = "MA_Cross", **kw):
        super().__init__(symbols, name=name, fast=fast, slow=slow,
                         allow_short=allow_short, **kw)
        self.fast = fast
        self.slow = slow
        self.allow_short = allow_short
        self._prev_fast: dict[str, float] = {}
        self._prev_slow: dict[str, float] = {}

    def on_bar(self, bar: Bar) -> None:
        closes = self.to_series(bar.symbol, "close")
        if len(closes) < self.slow:
            return

        fast_ma = closes.tail(self.fast).mean()
        slow_ma = closes.tail(self.slow).mean()
        prev_fast = self._prev_fast.get(bar.symbol)
        prev_slow = self._prev_slow.get(bar.symbol)
        self._prev_fast[bar.symbol] = fast_ma
        self._prev_slow[bar.symbol] = slow_ma

        if prev_fast is None or prev_slow is None:
            return

        pos = self.position(bar.symbol)
        crossed_up = prev_fast <= prev_slow and fast_ma > slow_ma
        crossed_dn = prev_fast >= prev_slow and fast_ma < slow_ma

        if crossed_up and pos <= 0:
            self.emit_signal(bar.symbol, Direction.LONG, strength=1.0)
        elif crossed_dn and pos > 0:
            if self.allow_short:
                self.emit_signal(bar.symbol, Direction.SHORT, strength=1.0)
            else:
                self.emit_signal(bar.symbol, Direction.EXIT, strength=1.0)


class TurtleBreakoutStrategy(Strategy):
    """Donchian-channel breakout. Long when close breaks above the N-day high;
    exit when it breaks below the M-day low (turtle defaults N=20, M=10)."""

    def __init__(self, symbols, entry: int = 20, exit: int = 10,
                 allow_short: bool = False, name: str = "Turtle", **kw):
        super().__init__(symbols, name=name, entry=entry, exit=exit,
                         allow_short=allow_short, **kw)
        self.entry = entry
        self.exit = exit
        self.allow_short = allow_short

    def on_bar(self, bar: Bar) -> None:
        highs = self.to_series(bar.symbol, "high")
        lows = self.to_series(bar.symbol, "low")
        if len(highs) < self.entry + 1:
            return

        # Use prior N days (exclude today) for the breakout level
        entry_high = highs.iloc[-(self.entry + 1):-1].max()
        exit_low = lows.iloc[-(self.exit + 1):-1].min()
        pos = self.position(bar.symbol)

        if bar.close > entry_high and pos <= 0:
            self.emit_signal(bar.symbol, Direction.LONG, strength=1.0)
        elif bar.close < exit_low and pos > 0:
            self.emit_signal(bar.symbol, Direction.EXIT, strength=1.0)
        elif bar.close < exit_low and pos < 0 and self.allow_short:
            pass  # already short
