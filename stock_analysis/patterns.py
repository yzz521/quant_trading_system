"""Pattern & signal detection — turn indicator series into human-readable
signals on the most recent bar.

Each function returns a short dict ``{"name", "type", "detail"}`` or ``None``
when no pattern fires. ``scan_signals`` runs them all and returns a list.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .indicators import MACD, MA, RSI, KDJ, BOLL


def _last(s: pd.Series):
    s = s.dropna()
    return s.iloc[-1] if len(s) else np.nan


def _prev(s: pd.Series):
    s = s.dropna()
    return s.iloc[-2] if len(s) >= 2 else np.nan


def ma_cross(close: pd.Series, fast: int = 5, slow: int = 20) -> Optional[dict]:
    ma_f, ma_s = MA(close, fast), MA(close, slow)
    f, p_f = _last(ma_f), _prev(ma_f)
    s, p_s = _last(ma_s), _prev(ma_s)
    if any(np.isnan(x) for x in (f, p_f, s, p_s)):
        return None
    if p_f <= p_s and f > s:
        return {"name": f"MA{fast}金叉MA{slow}", "type": "bull", "detail": "短均线上穿长均线，趋势转多"}
    if p_f >= p_s and f < s:
        return {"name": f"MA{fast}死叉MA{slow}", "type": "bear", "detail": "短均线下穿长均线，趋势转空"}
    return None


def macd_signal(close: pd.Series) -> Optional[dict]:
    m = MACD(close)
    dif, dea = m["dif"], m["dea"]
    d, p_d = _last(dif), _prev(dif)
    e, p_e = _last(dea), _prev(dea)
    if any(np.isnan(x) for x in (d, p_d, e, p_e)):
        return None
    if p_d <= p_e and d > e:
        return {"name": "MACD金叉", "type": "bull", "detail": "DIF上穿DEA，看多"}
    if p_d >= p_e and d < e:
        return {"name": "MACD死叉", "type": "bear", "detail": "DIF下穿DEA，看空"}
    return None


def rsi_level(close: pd.Series, n: int = 6) -> Optional[dict]:
    r = RSI(close, n)
    v = _last(r)
    if np.isnan(v):
        return None
    if v > 80:
        return {"name": f"RSI超买({v:.0f})", "type": "bear", "detail": "短期超买，回调风险"}
    if v < 20:
        return {"name": f"RSI超卖({v:.0f})", "type": "bull", "detail": "短期超卖，反弹机会"}
    return None


def kdj_signal(high: pd.Series, low: pd.Series, close: pd.Series) -> Optional[dict]:
    k = KDJ(high, low, close)
    j = k["j"]
    jv, p_j = _last(j), _prev(j)
    if np.isnan(jv) or np.isnan(p_j):
        return None
    if jv < 0:
        return {"name": f"KDJ超卖(J={jv:.0f})", "type": "bull", "detail": "J值负超卖，可能见底"}
    if jv > 100:
        return {"name": f"KDJ超买(J={jv:.0f})", "type": "bear", "detail": "J值超买，可能见顶"}
    return None


def boll_position(close: pd.Series, n: int = 20) -> Optional[dict]:
    b = BOLL(close, n)
    up, lo, mid = _last(b["upper"]), _last(b["lower"]), _last(b["mid"])
    c = _last(close)
    if any(np.isnan(x) for x in (up, lo, mid, c)):
        return None
    if c <= lo:
        return {"name": "触及布林下轨", "type": "bull", "detail": "价格跌破下轨，超卖反弹可能"}
    if c >= up:
        return {"name": "触及布林上轨", "type": "bear", "detail": "价格突破上轨，超买回调可能"}
    return None


def breakout(close: pd.Series, n: int = 20) -> Optional[dict]:
    """Donchian channel breakout on the latest bar."""
    if len(close) < n + 1:
        return None
    hh = close.iloc[-n - 1:-1].max()
    ll = close.iloc[-n - 1:-1].min()
    c = close.iloc[-1]
    if c > hh:
        return {"name": f"突破{n}日新高", "type": "bull", "detail": f"收盘{c:.2f}创{n}日新高，趋势向上"}
    if c < ll:
        return {"name": f"跌破{n}日新低", "type": "bear", "detail": f"收盘{c:.2f}创{n}日新低，趋势向下"}
    return None


def volume_anomaly(volume: pd.Series, n: int = 5) -> Optional[dict]:
    if len(volume) < n + 1:
        return None
    avg = volume.iloc[-n - 1:-1].mean()
    v = volume.iloc[-1]
    if avg <= 0:
        return None
    ratio = v / avg
    if ratio >= 2.0:
        return {"name": f"放量({ratio:.1f}倍)", "type": "bull", "detail": "成交量异常放大，关注资金动向"}
    if ratio <= 0.5:
        return {"name": f"缩量({ratio:.1f}倍)", "type": "neutral", "detail": "成交量明显萎缩"}
    return None


def kline_pattern(df: pd.DataFrame) -> Optional[dict]:
    """Single-bar candlestick patterns on the latest bar."""
    if len(df) < 1:
        return None
    r = df.iloc[-1]
    o, c, h, l = r["open"], r["close"], r["high"], r["low"]
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return None
    upper = h - max(o, c)
    lower = min(o, c) - l
    # Hammer / 倒锤头
    if lower > 2 * body and upper < body * 0.5 and c > o:
        return {"name": "锤子线", "type": "bull", "detail": "下影线长，底部反转信号"}
    if upper > 2 * body and lower < body * 0.5 and c < o:
        return {"name": "射击之星", "type": "bear", "detail": "上影线长，顶部反转信号"}
    # Doji
    if body <= 0.1 * rng:
        return {"name": "十字星", "type": "neutral", "detail": "多空均衡，变盘信号"}
    # Big bull/bear bar
    if body > 0.03 * c and c > o:
        return {"name": "大阳线", "type": "bull", "detail": "实体较长，多头强势"}
    if body > 0.03 * c and c < o:
        return {"name": "大阴线", "type": "bear", "detail": "实体较长，空头强势"}
    return None


def scan_signals(df: pd.DataFrame) -> list[dict]:
    """Run every detector on the latest bar; return all fired signals."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    detectors = [
        lambda: ma_cross(c),
        lambda: macd_signal(c),
        lambda: rsi_level(c),
        lambda: kdj_signal(h, l, c),
        lambda: boll_position(c),
        lambda: breakout(c),
        lambda: volume_anomaly(v),
        lambda: kline_pattern(df),
    ]
    out = []
    for det in detectors:
        try:
            r = det()
        except Exception:  # noqa: BLE001
            r = None
        if r:
            out.append(r)
    return out
