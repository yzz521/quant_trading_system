"""Synthetic data source — generates geometric-Brownian-motion OHLCV bars.

This exists so the system can be **run and validated without any network
access or third-party API**. It is perfect for unit tests, demos and
sanity-checking a strategy's logic against a controlled, known process.
For real research swap it for :class:`AkShareSource` / :class:`YFinanceSource`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from quant_trading_system.data import AkShareSource, YFinanceSource, AssetClass, DiskCache


from .data_source import AssetClass, DataSource


class SyntheticDataSource(DataSource):
    name = "synthetic"

    def __init__(
        self,
        asset_class: AssetClass = AssetClass.EQUITY_CN,
        seed: int = 42,
        annual_vol: float = 0.25,
        annual_drift: float = 0.05,
    ) -> None:
        super().__init__(asset_class)
        self.seed = seed
        self.annual_vol = annual_vol
        self.annual_drift = annual_drift

    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + hash(symbol) % 2**31)
        idx = pd.bdate_range(start=start, end=end)
        n = len(idx)
        if n == 0:
            return pd.DataFrame()

        dt = 1.0 / 252.0
        mu = self.annual_drift
        sigma = self.annual_vol
        # GBM close path
        rets = rng.normal((mu - 0.5 * sigma**2) * dt, sigma * np.sqrt(dt), size=n)
        close = 100.0 * np.exp(np.cumsum(rets))

        # Build intraday OHLC around each close
        open_ = close * (1 + rng.normal(0, sigma * np.sqrt(dt) * 0.5, size=n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, sigma * np.sqrt(dt) * 0.3, size=n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, sigma * np.sqrt(dt) * 0.3, size=n)))
        volume = rng.integers(1_000_000, 50_000_000, size=n).astype(float)

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        df.index.name = "datetime"
        # Add a gentle trend regime so trend-following strategies show signal
        if self.annual_drift != 0:
            df["close"] = df["close"] * np.linspace(1.0, 1.0 + 0.3 * np.sign(mu), n)
            df["open"] = df["open"] * np.linspace(1.0, 1.0 + 0.3 * np.sign(mu), n)
            df["high"] = df["high"] * np.linspace(1.0, 1.0 + 0.3 * np.sign(mu), n)
            df["low"] = df["low"] * np.linspace(1.0, 1.0 + 0.3 * np.sign(mu), n)
        return df
