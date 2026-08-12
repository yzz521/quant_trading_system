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
    c, h, l, v = out["close"], out["high"], out["low"], out["volume"]
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
    kdj = KDJ(h, l, c)
    out["k"], out["d"], out["j"] = kdj["k"], kdj["d"], kdj["j"]
    boll = BOLL(c)
    out["boll_upper"], out["boll_mid"], out["boll_lower"] = boll["upper"], boll["mid"], boll["lower"]
    out["atr"] = ATR(h, l, c)
    out["cci"] = CCI(h, l, c)
    out["wr"] = WR(h, l, c)
    out["obv"] = OBV(c, v)
    out["roc"] = ROC(c, 12)
    out["vr"] = VR(v, 5)
    return out


# --------------------------------------------------------------------------- #
# ADX 与文字化解读（解释逻辑移植自 ashare-analyzer，MIT, Copyright 2026 zwldarren；
# 文案与 explain_indicators 为本项目编写）
# --------------------------------------------------------------------------- #

def ADX(high, low, close, period: int = 14) -> pd.Series:
    """平均趋向指数 ADX(14)：衡量趋势强度，不区分方向。"""
    h = pd.Series(high, dtype=float)
    l = pd.Series(low, dtype=float)
    c = pd.Series(close, dtype=float)
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(
        up_move.where((up_move > down_move) & (up_move > 0), 0.0), index=h.index
    )
    minus_dm = pd.Series(
        down_move.where((down_move > up_move) & (down_move > 0), 0.0), index=l.index
    )
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    adx = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return adx


def interpret_rsi(rsi: float) -> str:
    """RSI 文字解读。"""
    if rsi >= 80:
        return "严重超买"
    if rsi >= 70:
        return "超买"
    if rsi >= 50:
        return "偏强"
    if rsi >= 30:
        return "偏弱"
    if rsi >= 20:
        return "超卖"
    return "严重超卖"


def interpret_stochastic(k: float, d: float) -> str:
    """KDJ 文字解读。"""
    if k >= 80 and d >= 80:
        return "超买"
    if k <= 20 and d <= 20:
        return "超卖"
    if k > d:
        return "金叉偏多"
    if k < d:
        return "死叉偏空"
    return "中性"


def interpret_macd(histogram: float) -> str:
    """MACD 柱状图文字解读。"""
    if histogram > 0:
        return "红柱（动能偏多）"
    if histogram < 0:
        return "绿柱（动能偏空）"
    return "零轴（中性）"


def interpret_adx(adx: float) -> str:
    """ADX 趋势强度解读。"""
    if adx >= 50:
        return "极强趋势"
    if adx >= 40:
        return "很强趋势"
    if adx >= 25:
        return "强趋势"
    if adx >= 20:
        return "趋势酝酿"
    return "无趋势/震荡"


def _get(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and not pd.isna(v):
            return v
    return None


def explain_indicators(row) -> list[str]:
    """把一行指标整理成中文解读列表（兼容 rsi12/RSI12 两种键名），供看板展示。"""
    out: list[str] = []
    rsi = _get(row, "rsi12", "RSI12")
    if rsi is not None:
        out.append(f"RSI(12)={rsi:.1f} · {interpret_rsi(float(rsi))}")
    hist = _get(row, "macd_hist", "MACD_Hist")
    if hist is not None:
        out.append(f"MACD柱={hist:+.4f} · {interpret_macd(float(hist))}")
    k, d = _get(row, "k", "K"), _get(row, "d", "D")
    if k is not None and d is not None:
        out.append(f"KDJ K={k:.1f} D={d:.1f} · {interpret_stochastic(float(k), float(d))}")
    adx = _get(row, "adx", "ADX")
    if adx is not None:
        out.append(f"ADX={adx:.1f} · {interpret_adx(float(adx))}")
    return out
