"""AkShare data source for A-shares, HK stocks and China futures.

AkShare is free and needs no API key, which makes it ideal for research and
backtesting. Column names from AkShare are Chinese and occasionally change
between versions, so we normalize defensively.
"""
from __future__ import annotations

import pandas as pd

from ..utils import get_logger
from .data_source import AssetClass, DataSource

# Chinese -> normalized column name
_CN_COL_MAP = {
    "日期": "datetime",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "开盘价": "open",
    "收盘价": "close",
    "最高价": "high",
    "最低价": "low",
}


class AkShareSource(DataSource):
    name = "akshare"

    def __init__(self, asset_class: AssetClass = AssetClass.EQUITY_CN) -> None:
        super().__init__(asset_class)
        self.log = get_logger(self.__class__.__name__)

    def _import_ak(self):
        try:
            import akshare as ak  # type: ignore
            return ak
        except ImportError as e:
            raise ImportError(
                "akshare is not installed. Run: pip install akshare"
            ) from e

    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        ak = self._import_ak()
        sd = pd.to_datetime(start).strftime("%Y%m%d")
        ed = pd.to_datetime(end).strftime("%Y%m%d")
        period = {"1d": "daily", "1w": "weekly", "1M": "monthly"}.get(frequency, "daily")

        try:
            if self.asset_class == AssetClass.EQUITY_CN:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period=period, start_date=sd, end_date=ed, adjust=adjust
                )
            elif self.asset_class == AssetClass.EQUITY_HK:
                df = ak.stock_hk_hist(
                    symbol=symbol, period=period, start_date=sd, end_date=ed, adjust=adjust
                )
            elif self.asset_class == AssetClass.FUTURE_CN:
                # Sina futures daily; symbol like 'RB0' (main continuous)
                df = ak.futures_zh_daily_sina(symbol=symbol)
                df = df.rename(columns={"date": "datetime"})
            else:
                raise ValueError(f"AkShare does not serve {self.asset_class}")
        except Exception as e:  # noqa: BLE001
            self.log.error("AkShare fetch failed for %s: %s", symbol, e)
            return pd.DataFrame()

        return self._normalize(df, start, end)

    def _normalize(self, df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={k: v for k, v in _CN_COL_MAP.items() if k in df.columns})
        # Fallback: lowercase english columns directly
        df.columns = [c.lower() if c not in ("datetime",) else c for c in df.columns]

        if "datetime" not in df.columns:
            # pick the first column that looks like a date
            for c in df.columns:
                if "date" in c.lower():
                    df = df.rename(columns={c: "datetime"})
                    break

        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()

        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep]
        for c in keep:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df.loc[start:end]
        return df
