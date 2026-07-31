"""Multi-factor cross-sectional stock-selection strategy.

On each rebalance day this strategy:

1. Computes a small factor set for every symbol in the universe
   (momentum, reversal, low-volatility, RSI).
2. Cross-sectionally z-scores each factor and combines them with equal
   weight into a composite score.
3. Goes long the top-N names, exits everything outside the top-N.

Because the engine delivers bars one symbol at a time, the strategy waits
until the trading *day* rolls over before scoring — by then every symbol's
previous-day close is already in the rolling buffer, so the cross section is
complete.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..core import Bar, Direction
from .base import Strategy


def _rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return 50.0
    delta = series.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(period).mean().iloc[-1]
    if loss == 0:
        return 100.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


class MultiFactorStrategy(Strategy):
    """Equal-weight composite of momentum / reversal / low-vol / RSI factors.

    Params
    ------
    rebalance_days : int   re-balance every N trading days (default 5)
    top_n : int            number of long names to hold (default 5)
    """

    def __init__(self, symbols, rebalance_days: int = 5, top_n: int = 5,
                 name: str = "MultiFactor", **kw):
        super().__init__(symbols, name=name, rebalance_days=rebalance_days,
                         top_n=top_n, **kw)
        self.rebalance_days = rebalance_days
        self.top_n = top_n
        self._last_date: date | None = None
        self._day_count = 0
        self._current_holdings: set[str] = set()

    def on_bar(self, bar: Bar) -> None:
        cur_date = bar.datetime.date()
        if self._last_date is not None and cur_date != self._last_date:
            # A new trading day started -> previous day's bars are all cached.
            self._day_count += 1
            if self._day_count % self.rebalance_days == 0:
                self._rebalance(as_of=self._last_date)
        self._last_date = cur_date

    def _factor_snapshot(self, symbol: str) -> dict[str, float] | None:
        closes = self.to_series(symbol, "close")
        if len(closes) < 25:
            return None
        mom20 = closes.iloc[-1] / closes.iloc[-21] - 1.0
        rev5 = -(closes.iloc[-1] / closes.iloc[-6] - 1.0)   # short-term reversal
        vol20 = -closes.tail(20).pct_change().std()         # low-vol (negative)
        rsi = -(_rsi(closes, 14) - 50.0)                    # oversold tilt
        return {"momentum": mom20, "reversal": rev5,
                "lowvol": vol20, "rsi": rsi}

    def _rebalance(self, as_of: date) -> None:
        rows = []
        for sym in self.symbols:
            snap = self._factor_snapshot(sym)
            if snap:
                snap["symbol"] = sym
                rows.append(snap)
        if len(rows) < self.top_n:
            return
        df = pd.DataFrame(rows).set_index("symbol")

        # Cross-sectional z-score per factor
        factors = ["momentum", "reversal", "lowvol", "rsi"]
        z = (df[factors] - df[factors].mean()) / (df[factors].std(ddof=0).replace(0, np.nan))
        df["score"] = z.mean(axis=1)

        top = df["score"].nlargest(self.top_n).index.tolist()
        new_holdings = set(top)

        # Exit names that fell out of the top-N
        for sym in self._current_holdings - new_holdings:
            self.emit_signal(sym, Direction.EXIT, strength=1.0)
        # Enter new top names
        for sym in new_holdings - self._current_holdings:
            self.emit_signal(sym, Direction.LONG, strength=1.0 / self.top_n)

        self._current_holdings = new_holdings
        self.log.info("Rebalance %s: long %s", as_of, sorted(new_holdings))
