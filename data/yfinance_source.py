"""yfinance data source for US equities, ETFs, FX and commodities (e.g. gold).

yfinance is free and unofficial but good enough for research. For real-money
US trading you would switch to Interactive Brokers / Polygon.io.
"""
from __future__ import annotations

import pandas as pd

from ..utils import get_logger
from .data_source import AssetClass, DataSource


class YFinanceSource(DataSource):
    name = "yfinance"

    def __init__(self, asset_class: AssetClass = AssetClass.EQUITY_US) -> None:
        super().__init__(asset_class)
        self.log = get_logger(self.__class__.__name__)

    def _import_yf(self):
        try:
            import yfinance as yf  # type: ignore
            return yf
        except ImportError as e:
            raise ImportError(
                "yfinance is not installed. Run: pip install yfinance"
            ) from e

    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        yf = self._import_yf()
        interval = {"1d": "1d", "1w": "1wk", "1M": "1mo"}.get(frequency, "1d")
        # yfinance's end date is exclusive-ish; bump by one day to be safe.
        end_excl = (pd.to_datetime(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            df = yf.download(
                symbol,
                start=start,
                end=end_excl,
                interval=interval,
                auto_adjust=(adjust in ("qfq", "hfq")),
                progress=False,
            )
        except Exception as e:  # noqa: BLE001
            self.log.error("yfinance fetch failed for %s: %s", symbol, e)
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        # yfinance may return MultiIndex columns when a single ticker is passed
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        # Drop 'adj_close' when auto_adjust=True (column absent) — keep consistent
        if "adj_close" in df.columns and "close" in df.columns:
            df = df.drop(columns=["adj_close"])

        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep]
        df.index = pd.to_datetime(df.index)
        df.index.name = "datetime"
        df = df.sort_index()
        for c in keep:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        return df
