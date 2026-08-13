"""Technical indicators — pure pandas/numpy, no TA-Lib dependency.

Every function takes a DataFrame with at least ``close`` (and ``high``/``low``
/``volume`` where relevant) and returns the indicator as a Series or a dict of
Series, ready to be concatenated into a feature frame.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #
def MA(close: pd.Series, n: int = 20) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def EMA(close: pd.Series, n: int = 12) -> pd.Series:
    return close.ewm(span=n, adjust=False).mean()


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #
def MACD(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ema_fast = EMA(close, fast)
    ema_slow = EMA(close, slow)
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return {"dif": dif, "dea": dea, "hist": hist}


# --------------------------------------------------------------------------- #
# RSI (Wilder)
# --------------------------------------------------------------------------- #
def RSI(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50)


# --------------------------------------------------------------------------- #
# KDJ
# --------------------------------------------------------------------------- #
def KDJ(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3) -> dict:
    low_n = low.rolling(n, min_periods=1).min()
    high_n = high.rolling(n, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return {"k": k, "d": d, "j": j}


# --------------------------------------------------------------------------- #
# Bollinger Bands
# --------------------------------------------------------------------------- #
def BOLL(close: pd.Series, n: int = 20, num_std: float = 2.0) -> dict:
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return {"upper": upper, "mid": mid, "lower": lower, "width": (upper - lower) / mid}


# --------------------------------------------------------------------------- #
# ATR (Average True Range)
# --------------------------------------------------------------------------- #
def ATR(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# --------------------------------------------------------------------------- #
# CCI
# --------------------------------------------------------------------------- #
def CCI(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    ma_tp = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma_tp) / (0.015 * md.replace(0, np.nan))


# --------------------------------------------------------------------------- #
# Williams %R
# --------------------------------------------------------------------------- #
def WR(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    hh = high.rolling(n).max()
    ll = low.rolling(n).min()
    return (hh - close) / (hh - ll).replace(0, np.nan) * -100


# --------------------------------------------------------------------------- #
# OBV (On-Balance Volume)
# --------------------------------------------------------------------------- #
def OBV(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


# --------------------------------------------------------------------------- #
# ROC (Rate of Change)
# --------------------------------------------------------------------------- #
def ROC(close: pd.Series, n: int = 12) -> pd.Series:
    return close.pct_change(n) * 100


# --------------------------------------------------------------------------- #
# Volume ratio (量比) — today's avg vol vs N-day avg vol
# --------------------------------------------------------------------------- #
def VR(volume: pd.Series, n: int = 5) -> pd.Series:
    return volume / volume.rolling(n).mean().replace(0, np.nan)


# --------------------------------------------------------------------------- #
# Convenience: attach all indicators to a frame
# --------------------------------------------------------------------------- #
def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with every indicator below attached as columns."""
    out = df.copy()
    c, h, lo, v = out["close"], out["high"], out["low"], out["volume"]
    out["ma5"] = MA(c, 5)
    out["ma10"] = MA(c, 10)
    out["ma20"] = MA(c, 20)
    out["ma60"] = MA(c, 60)
    out["ema12"] = EMA(c, 12)
    macd = MACD(c)
    out["macd_dif"], out["macd_dea"], out["macd_hist"] = macd["dif"], macd["dea"], macd["hist"]
    out["rsi6"] = RSI(c, 6)
    out["rsi12"] = RSI(c, 12)
    out["rsi24"] = RSI(c, 24)
    kdj = KDJ(h, lo, c)
    out["k"], out["d"], out["j"] = kdj["k"], kdj["d"], kdj["j"]
    boll = BOLL(c)
    out["boll_upper"], out["boll_mid"], out["boll_lower"] = boll["upper"], boll["mid"], boll["lower"]
    out["atr"] = ATR(h, lo, c)
    out["cci"] = CCI(h, lo, c)
    out["wr"] = WR(h, lo, c)
    out["obv"] = OBV(c, v)
    out["roc"] = ROC(c, 12)
    out["vr"] = VR(v, 5)
    return out
