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
# ADX / DMI (Wilder)
# --------------------------------------------------------------------------- #
def ADX(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> dict:
    """Average Directional Index + DI. ``adx`` 趋势强度，``plus_di``/``minus_di`` 方向。"""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    alpha = 1.0 / n
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_s = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_s = minus_dm.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_s / atr.replace(0, np.nan)
    minus_di = 100.0 * minus_s / atr.replace(0, np.nan)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


# --------------------------------------------------------------------------- #
# Rolling VWAP (volume-weighted typical price, default 20 bars)
# --------------------------------------------------------------------------- #
def VWAP(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, n: int = 20) -> pd.Series:
    """Rolling VWAP. Daily bars have no session VWAP; this is the N-day anchored mean."""
    tp = (high + low + close) / 3.0
    pv = tp * volume
    den = volume.rolling(n, min_periods=n).sum().replace(0, np.nan)
    return pv.rolling(n, min_periods=n).sum() / den


# --------------------------------------------------------------------------- #
# Fibonacci retracement from the lookback swing
# --------------------------------------------------------------------------- #
FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fibonacci_retracement(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 60,
) -> dict[str, float]:
    """Swing high/low retracement levels (absolute prices). Empty dict if no range."""
    h = pd.to_numeric(high.tail(lookback), errors="coerce")
    l = pd.to_numeric(low.tail(lookback), errors="coerce")
    if h.empty or l.empty or h.isna().all() or l.isna().all():
        return {}
    swing_h = float(h.max())
    swing_l = float(l.min())
    rng = swing_h - swing_l
    if rng <= 0 or not np.isfinite(rng):
        return {}
    out: dict[str, float] = {"high": round(swing_h, 2), "low": round(swing_l, 2)}
    key = {0.236: "fib_236", 0.382: "fib_382", 0.5: "fib_500", 0.618: "fib_618", 0.786: "fib_786"}
    for r in FIB_RATIOS:
        out[key[r]] = round(swing_h - rng * r, 2)
    return out


# --------------------------------------------------------------------------- #
# Composite signal grade (display / confirmation — not a 10th ranking factor)
# --------------------------------------------------------------------------- #
def rate_signals(df: pd.DataFrame) -> dict:
    """Confluence snapshot: grade S/A/B/C, 0-100 score, short Chinese tags."""
    empty = {"grade": "C", "score": 50.0, "tags": []}
    if df is None or df.empty:
        return empty
    last = df.iloc[-1]
    score = 50.0
    tags: list[str] = []

    def _f(col: str):
        if col not in df.columns:
            return None
        v = last[col]
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if np.isnan(v) else v

    close = _f("close")
    ma5, ma20, ma60 = _f("ma5"), _f("ma20"), _f("ma60")
    if close and ma5 and ma20 and ma60:
        if ma5 > ma20 > ma60 and close > ma20:
            score += 18
            tags.append("均线多头")
        elif ma5 < ma20 < ma60 and close < ma20:
            score -= 18
            tags.append("均线空头")
        elif close > ma20:
            score += 6
            tags.append("价在MA20上")

    dif, dea, hist = _f("macd_dif"), _f("macd_dea"), _f("macd_hist")
    if dif is not None and dea is not None:
        if dif > dea and (hist or 0) >= 0:
            score += 10
            tags.append("MACD多头")
        elif dif < dea and (hist or 0) <= 0:
            score -= 10
            tags.append("MACD空头")

    adx, pdi, mdi = _f("adx"), _f("plus_di"), _f("minus_di")
    if adx is not None:
        if adx >= 25 and pdi is not None and mdi is not None:
            if pdi > mdi:
                score += 12
                tags.append("ADX趋势强")
            else:
                score -= 12
                tags.append("ADX空头趋势")
        elif adx < 15:
            score -= 8
            tags.append("ADX无趋势")

    vwap = _f("vwap20")
    if close and vwap:
        if close >= vwap:
            score += 6
            tags.append("价在VWAP上")
        else:
            score -= 4

    rsi = _f("rsi12")
    if rsi is not None:
        if 55 <= rsi <= 75:
            score += 8
            tags.append("RSI健康")
        elif rsi > 80:
            score -= 10
            tags.append("RSI超买")
        elif rsi < 40:
            score -= 8
            tags.append("RSI偏弱")

    width = _f("boll_width")
    if width is not None and 0 < width < 0.04:
        tags.append("布林挤压")

    score = float(np.clip(score, 0, 100))
    if score >= 80:
        grade = "S"
    elif score >= 65:
        grade = "A"
    elif score >= 50:
        grade = "B"
    else:
        grade = "C"
    return {"grade": grade, "score": round(score, 1), "tags": tags[:6]}


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
    out["ma120"] = MA(c, 120)
    out["ma250"] = MA(c, 250)
    out["ema12"] = EMA(c, 12)
    out["ema20"] = EMA(c, 20)
    macd = MACD(c)
    out["macd_dif"], out["macd_dea"], out["macd_hist"] = macd["dif"], macd["dea"], macd["hist"]
    out["rsi6"] = RSI(c, 6)
    out["rsi12"] = RSI(c, 12)
    out["rsi24"] = RSI(c, 24)
    kdj = KDJ(h, lo, c)
    out["k"], out["d"], out["j"] = kdj["k"], kdj["d"], kdj["j"]
    boll = BOLL(c)
    out["boll_upper"], out["boll_mid"], out["boll_lower"] = boll["upper"], boll["mid"], boll["lower"]
    out["boll_width"] = boll["width"]
    out["atr"] = ATR(h, lo, c)
    out["cci"] = CCI(h, lo, c)
    out["wr"] = WR(h, lo, c)
    out["obv"] = OBV(c, v)
    out["roc"] = ROC(c, 12)
    out["vr"] = VR(v, 5)
    out["vol_ma5"] = MA(v, 5)
    out["vol_ma20"] = MA(v, 20)
    adx = ADX(h, lo, c)
    out["adx"], out["plus_di"], out["minus_di"] = adx["adx"], adx["plus_di"], adx["minus_di"]
    out["vwap20"] = VWAP(h, lo, c, v, 20)
    return out
