"""Bar feed — merges multiple symbols into a time-sorted event stream.

For backtesting the feed is pre-loaded with historical frames; for live
trading you would implement a real-time ``DataFeed`` subclass that pushes
``MarketEvent`` objects onto the engine as ticks/bars arrive from the broker.
"""
from __future__ import annotations

from typing import Iterator

import pandas as pd

from ..core import Bar, MarketEvent
from ..utils import get_logger


class DataFeed:
    """Abstract feed: produces MarketEvents in chronological order."""

    def stream(self, engine) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class BarFeed(DataFeed):
    """Backtest feed. Holds a dict of symbol -> OHLCV DataFrame and replays
    them bar-by-bar in time order, aligning symbols that share a timestamp.

    Usage::

        feed = BarFeed({"AAPL": df_aapl, "600000": df_aapl_cn})
        for ts, bars in feed:
            ...
    """

    def __init__(self, data: dict[str, pd.DataFrame], frequency: str = "1d",
                 calendar_market: str | None = None) -> None:
        # Drop empty frames and validate columns
        cleaned: dict[str, pd.DataFrame] = {}
        for sym, df in data.items():
            if df is None or df.empty:
                continue
            if "close" not in df.columns:
                raise ValueError(f"DataFrame for {sym} missing 'close' column")
            cleaned[sym] = df.copy()
        self.data = cleaned
        self.frequency = frequency
        self.calendar_market = calendar_market
        self.timeline = self._build_timeline()
        self.log = get_logger(self.__class__.__name__)
        self.log.info("BarFeed ready: %d symbols, %d timestamps",
                      len(cleaned), len(self.timeline))

    def _build_timeline(self) -> list[pd.Timestamp]:
        if not self.data:
            return []
        union = None
        for df in self.data.values():
            union = df.index if union is None else union.union(df.index)
        stamps = sorted(set(union))
        if self.calendar_market:
            try:
                from ..utils.calendar import is_trading_day
                stamps = [ts for ts in stamps if is_trading_day(ts, market=self.calendar_market)]
            except Exception:
                pass
        return stamps

    def __len__(self) -> int:
        return len(self.timeline)

    def __iter__(self) -> Iterator[tuple[pd.Timestamp, list[Bar]]]:
        for ts in self.timeline:
            bars: list[Bar] = []
            for sym, df in self.data.items():
                if ts in df.index:
                    row = df.loc[ts]
                    bars.append(Bar.from_series(sym, ts.to_pydatetime(), row, self.frequency))
            yield ts, bars

    def stream(self, engine) -> None:
        """Replay every bar through the engine. The engine must register its
        own handlers before calling this. After each timestamp the queue is
        drained so strategies react to one bar at a time."""
        for ts, bars in self:
            for bar in bars:
                engine.put(MarketEvent(bar=bar, timestamp=bar.datetime))
            engine.run_once()

    def latest_close(self, symbol: str) -> float | None:
        df = self.data.get(symbol)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])
