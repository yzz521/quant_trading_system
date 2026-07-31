"""Technical feature engineering for ML strategies.

All functions take a close-price ``pd.Series`` and return a DataFrame of
features aligned to the input index. Designed to be composed freely.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def build_technical_features(close: pd.Series) -> pd.DataFrame:
    """Return a DataFrame of common technical features."""
    ret1 = close.pct_change(1)
    ret5 = close.pct_change(5)
    ret20 = close.pct_change(20)
    vol20 = ret1.rolling(20).std()
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    feat = pd.DataFrame({
        "ret1": ret1,
        "ret5": ret5,
        "ret20": ret20,
        "vol20": vol20,
        "rsi": rsi(close, 14),
        "ma_bias_20": (close - ma20) / ma20,
        "ma_bias_60": (close - ma60) / ma60,
        "ma_cross": (ma5 - ma20) / ma20,
        "volume_z": np.nan,  # placeholder; supply volume externally if desired
    })
    return feat.replace([np.inf, -np.inf], np.nan)


def label_forward_return(close: pd.Series, horizon: int = 1,
                         threshold: float = 0.0) -> pd.Series:
    """Classification label: 1 if forward return > threshold else 0."""
    fwd = close.shift(-horizon) / close - 1.0
    return (fwd > threshold).astype(float).where(fwd.notna(), np.nan)
