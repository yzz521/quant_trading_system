"""Mean-reversion strategies.

:class:`BollingerBandStrategy` — buy when price tags the lower band, exit at
the middle band, optionally short at the upper band. Works best on ranging,
mean-reverting instruments.
"""
from __future__ import annotations

import numpy as np

from ..core import Bar, Direction
from .base import Strategy


class BollingerBandStrategy(Strategy):
    """Bollinger band mean reversion.

    Params
    ------
    window : int        MA / std window (default 20)
    num_std : float     band width in standard deviations (default 2.0)
    allow_short : bool  short when price hits upper band (default False)
    """

    def __init__(self, symbols, window: int = 20, num_std: float = 2.0,
                 allow_short: bool = False, name: str = "Bollinger", **kw):
        super().__init__(symbols, name=name, window=window, num_std=num_std,
                         allow_short=allow_short, **kw)
        self.window = window
        self.num_std = num_std
        self.allow_short = allow_short

    def on_bar(self, bar: Bar) -> None:
        closes = self.to_series(bar.symbol, "close")
        if len(closes) < self.window:
            return

        window_closes = closes.tail(self.window)
        mid = window_closes.mean()
        std = window_closes.std(ddof=0)
        upper = mid + self.num_std * std
        lower = mid - self.num_std * std

        pos = self.position(bar.symbol)

        # Long entry: close below lower band
        if bar.close < lower and pos <= 0:
            self.emit_signal(bar.symbol, Direction.LONG, strength=1.0)
        # Exit long: reverted to middle band
        elif pos > 0 and bar.close >= mid:
            self.emit_signal(bar.symbol, Direction.EXIT, strength=1.0)

        if self.allow_short:
            if bar.close > upper and pos >= 0:
                self.emit_signal(bar.symbol, Direction.SHORT, strength=1.0)
            elif pos < 0 and bar.close <= mid:
                self.emit_signal(bar.symbol, Direction.EXIT, strength=1.0)
