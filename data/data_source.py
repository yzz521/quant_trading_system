"""Abstract data source and asset classification.

Every concrete vendor (AkShare, yfinance, CTP, Binance, ...) implements the
same interface so the rest of the system never needs to know where a bar
came from.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

import pandas as pd


class AssetClass(str, Enum):
    EQUITY_CN = "equity_cn"      # A股
    EQUITY_HK = "equity_hk"      # 港股
    EQUITY_US = "equity_us"      # 美股
    FUTURE_CN = "future_cn"      # 国内期货
    CRYPTO = "crypto"            # 加密货币
    FX = "fx"                    # 外汇
    COMMODITY = "commodity"      # 大宗商品/黄金


class DataSource(ABC):
    """Base class for all historical/realtime market data providers."""

    name: str = "base"

    def __init__(self, asset_class: AssetClass = AssetClass.EQUITY_CN) -> None:
        self.asset_class = asset_class

    @abstractmethod
    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",  # qfq(前复权)/hfq(后复权)/none
    ) -> pd.DataFrame:
        """Return OHLCV history as a DataFrame with a DatetimeIndex.

        Columns are normalized to lowercase: ``open, high, low, close, volume``.
        """
        raise NotImplementedError

    def list_symbols(self) -> list[str]:
        """Optional: return tradable symbols for this source."""
        return []

    # Convenience wrapper used by feeds.
    def get_history_cached(
        self,
        symbol: str,
        start: str,
        end: str,
        cache: Optional["DiskCache"] = None,
        frequency: str = "1d",
        adjust: str = "qfq",
        *,
        validate: bool = True,
        strict: bool = False,
    ) -> pd.DataFrame:
        if cache is not None:
            cached = cache.get(symbol, start, end, frequency, adjust)
            if cached is not None:
                df = cached
            else:
                df = self.get_history(symbol, start, end, frequency=frequency, adjust=adjust)
                if cache is not None and not df.empty:
                    cache.set(symbol, df, frequency, adjust, source=self.name, adjust_flag=adjust)
        else:
            df = self.get_history(symbol, start, end, frequency=frequency, adjust=adjust)

        if validate and df is not None and not df.empty:
            from .quality import normalize_columns, validate_ohlcv
            df = normalize_columns(df)
            issues = validate_ohlcv(df, symbol=symbol)
            if issues:
                msg = "; ".join(issues)
                if strict:
                    raise ValueError(f"data quality failed for {symbol}: {msg}")
                # soft: keep data but callers can log
                try:
                    from ..utils import get_logger
                    get_logger(self.__class__.__name__).warning("OHLCV quality %s: %s", symbol, msg)
                except Exception:
                    pass
        return df
