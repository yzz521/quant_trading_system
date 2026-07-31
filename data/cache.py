"""Simple parquet/CSV disk cache so repeated backtests don't re-download.

Cache keys are derived from symbol + frequency + adjust + date range.
Stale-but-overlapping ranges are merged: if the requested range is fully
covered by a cached frame we slice it; otherwise we fetch fresh and overwrite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from ..utils import get_logger, ensure_dir


class DiskCache:
    def __init__(self, root: str | Path = "results/data_cache") -> None:
        self.root = ensure_dir(root)
        self.log = get_logger(self.__class__.__name__)

    def _path(self, symbol: str, frequency: str, adjust: str) -> Path:
        safe = symbol.replace("/", "_").replace(".", "_")
        return self.root / f"{safe}_{frequency}_{adjust}.parquet"

    def get(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str,
        adjust: str,
    ) -> Optional[pd.DataFrame]:
        path = self._path(symbol, frequency, adjust)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df = df.loc[start:end]
            if df.empty:
                return None
            # If the cached range doesn't fully cover the request, refetch.
            if pd.to_datetime(start) < df.index.min() or pd.to_datetime(end) > df.index.max():
                return None
            return df.copy()
        except Exception:  # noqa: BLE001
            self.log.warning("Corrupt cache file %s, refetching", path)
            return None

    def set(self, symbol: str, df: pd.DataFrame, frequency: str, adjust: str) -> None:
        path = self._path(symbol, frequency, adjust)
        try:
            df.to_parquet(path)
        except Exception:  # noqa: BLE001
            # Fallback to CSV if parquet engine missing.
            df.to_csv(path.with_suffix(".csv"))
