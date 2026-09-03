"""Technical indicator helpers (ADX / VWAP / Fibonacci / composite grade)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from quant_trading_system.stock_analysis.indicators import (
    ADX,
    add_all_indicators,
    fibonacci_retracement,
    rate_signals,
)


def _kline(n=160, seed=7, trend=0.03, vol=0.12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}
    )
    return df


def test_add_all_has_adx_vwap_ma120():
    df = add_all_indicators(_kline())
    for col in ("adx", "plus_di", "minus_di", "vwap20", "ma120", "ma250", "boll_width", "vol_ma5"):
        assert col in df.columns
    last = df.iloc[-1]
    assert 0 <= float(last["adx"]) <= 100
    assert float(last["vwap20"]) > 0


def test_adx_range():
    raw = _kline(trend=0.08, seed=1)
    out = ADX(raw["high"], raw["low"], raw["close"])
    adx = float(out["adx"].iloc[-1])
    assert 0 <= adx <= 100
    assert not np.isnan(adx)


def test_fibonacci_order():
    raw = _kline()
    fib = fibonacci_retracement(raw["high"], raw["low"], lookback=60)
    assert fib["high"] > fib["low"]
    assert fib["fib_236"] > fib["fib_382"] > fib["fib_500"] > fib["fib_618"] > fib["fib_786"]
    assert fib["low"] <= fib["fib_786"] <= fib["high"]


def test_rate_signals_grade():
    df = add_all_indicators(_kline(trend=0.08, seed=3))
    sig = rate_signals(df)
    assert sig["grade"] in ("S", "A", "B", "C")
    assert 0 <= sig["score"] <= 100
    assert isinstance(sig["tags"], list)
