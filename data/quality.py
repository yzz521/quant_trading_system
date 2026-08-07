"""行情 OHLCV 基础质量检查（可选，不自动挂载到 DataSource）。"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


REQUIRED = ("open", "high", "low", "close", "volume")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    return out


def validate_ohlcv(
    df: pd.DataFrame,
    symbol: str = "",
    max_abs_return: float = 0.25,
) -> list[str]:
    """返回问题描述列表；空列表表示通过。"""
    issues: list[str] = []
    if df is None or df.empty:
        return [f"{symbol}: empty dataframe"]
    d = normalize_columns(df)
    missing = [c for c in REQUIRED if c not in d.columns]
    if missing:
        issues.append(f"{symbol}: missing columns {missing}")
        return issues
    if (d[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append(f"{symbol}: non-positive OHLC")
    bad_hl = d["high"] < d["low"]
    if bad_hl.any():
        issues.append(f"{symbol}: high < low on {int(bad_hl.sum())} rows")
    rets = d["close"].pct_change().abs()
    spikes = rets > max_abs_return
    if spikes.any():
        issues.append(
            f"{symbol}: {int(spikes.sum())} bars with |return| > {max_abs_return:.0%}"
        )
    return issues


def assert_ohlcv(df: pd.DataFrame, symbol: str = "", **kwargs) -> pd.DataFrame:
    issues = validate_ohlcv(df, symbol=symbol, **kwargs)
    if issues:
        raise ValueError("; ".join(issues))
    return normalize_columns(df)
