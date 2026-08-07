"""Read OHLCV from a local directory of parquet/csv files (offline fallback)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..utils import get_logger
from .data_source import AssetClass, DataSource
from .quality import normalize_columns


class LocalParquetSource(DataSource):
    name = "local_parquet"

    def __init__(
        self,
        root: str | Path,
        asset_class: AssetClass = AssetClass.EQUITY_CN,
    ) -> None:
        super().__init__(asset_class)
        self.root = Path(root)
        self.log = get_logger(self.__class__.__name__)

    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        candidates = [
            self.root / f"{symbol}_{frequency}_{adjust}.parquet",
            self.root / f"{symbol}.parquet",
            self.root / f"{symbol}.csv",
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            self.log.warning("No local file for %s under %s", symbol, self.root)
            return pd.DataFrame()
        try:
            if path.suffix == ".csv":
                df = pd.read_csv(path, index_col=0, parse_dates=True)
            else:
                df = pd.read_parquet(path)
            df = normalize_columns(df)
            df = df.loc[pd.to_datetime(start) : pd.to_datetime(end)]
            return df.copy()
        except Exception as e:  # noqa: BLE001
            self.log.error("Local read failed %s: %s", path, e)
            return pd.DataFrame()
