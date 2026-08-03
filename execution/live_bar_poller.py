"""Poll a DataSource on an interval and push MarketEvents into the engine.

Used for Paper closed-loop with real (or synthetic) bars without a vendor SDK.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd

from ..core import Bar, MarketEvent
from ..data.data_source import DataSource
from ..utils import get_logger


class LiveBarPoller:
    def __init__(
        self,
        engine,
        source: DataSource,
        symbols: list[str],
        *,
        interval_sec: float = 60.0,
        lookback_days: int = 5,
        frequency: str = "1d",
        on_bar: Optional[Callable[[Bar], None]] = None,
    ) -> None:
        self.engine = engine
        self.source = source
        self.symbols = list(symbols)
        self.interval_sec = interval_sec
        self.lookback_days = lookback_days
        self.frequency = frequency
        self.on_bar = on_bar
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_ts: dict[str, datetime] = {}
        self.log = get_logger(self.__class__.__name__)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log.info(
            "LiveBarPoller started symbols=%s interval=%.1fs",
            self.symbols,
            self.interval_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_sec + 2)

    def poll_once(self) -> int:
        """Fetch once and push any new bars. Returns number of bars pushed."""
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)
        n = 0
        for sym in self.symbols:
            try:
                df = self.source.get_history(
                    sym,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                    frequency=self.frequency,
                )
            except Exception as e:  # noqa: BLE001
                self.log.warning("poll %s failed: %s", sym, e)
                continue
            if df is None or df.empty:
                continue
            df = df.sort_index()
            last = self._last_ts.get(sym)
            for ts, row in df.iterrows():
                ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else pd.Timestamp(ts).to_pydatetime()
                if last is not None and ts_dt <= last:
                    continue
                bar = Bar.from_series(sym, ts_dt, row, self.frequency)
                self.engine.event_engine.put(MarketEvent(bar=bar, timestamp=bar.datetime))
                if self.on_bar:
                    self.on_bar(bar)
                self._last_ts[sym] = ts_dt
                n += 1
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = self.poll_once()
                if n:
                    self.log.info("Pushed %d new bars", n)
            except Exception:
                self.log.exception("poll loop error")
            self._stop.wait(self.interval_sec)
