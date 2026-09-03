"""K-line pattern detection — confirmation tags, not a primary ranking factor.

Single bar: doji / hammer / shooting star / long yang / long yin.
Two bar: bullish / bearish engulfing.
Three bar: morning star / evening star / three white soldiers / three black crows.

A-share daily bars often have no gap; star patterns do not require a gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CandlePattern:
    name: str
    kind: str          # single / double / triple
    direction: str     # bull / bear / neutral
    bars: int


# Signed contribution to similar_pattern score (clipped later).
_PATTERN_DELTA = {
    "锤子线": 18,
    "看涨吞没": 20,
    "晨星": 22,
    "三白兵": 16,
    "大阳线": 8,
    "射击之星": -18,
    "看跌吞没": -20,
    "暮星": -22,
    "三黑鸦": -16,
    "大阴线": -8,
    "十字星": 0,
}


def _ohlc(row) -> tuple[float, float, float, float]:
    return float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])


def _rng(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _is_doji(o: float, h: float, l: float, c: float, thr: float = 0.12) -> bool:
    return _body(o, c) / _rng(h, l) <= thr


def _is_hammer(o: float, h: float, l: float, c: float) -> bool:
    body = _body(o, c)
    rng = _rng(h, l)
    lower = min(o, c) - l
    upper = h - max(o, c)
    return lower >= 2.0 * max(body, rng * 0.05) and upper <= max(body, rng * 0.05) * 1.2 and body / rng <= 0.4


def _is_shooting_star(o: float, h: float, l: float, c: float) -> bool:
    body = _body(o, c)
    rng = _rng(h, l)
    lower = min(o, c) - l
    upper = h - max(o, c)
    return upper >= 2.0 * max(body, rng * 0.05) and lower <= max(body, rng * 0.05) * 1.2 and body / rng <= 0.4


def _is_long_yang(o: float, h: float, l: float, c: float, avg_body: float) -> bool:
    rng = _rng(h, l)
    body = c - o
    return body > 0 and body / rng >= 0.6 and body >= 1.4 * max(avg_body, rng * 0.02)


def _is_long_yin(o: float, h: float, l: float, c: float, avg_body: float) -> bool:
    rng = _rng(h, l)
    body = o - c
    return body > 0 and body / rng >= 0.6 and body >= 1.4 * max(avg_body, rng * 0.02)


def _bullish_engulfing(p, c) -> bool:
    o0, _, _, c0 = _ohlc(p)
    o1, _, _, c1 = _ohlc(c)
    return c0 < o0 and c1 > o1 and o1 <= c0 and c1 >= o0 and _body(o1, c1) > _body(o0, c0)


def _bearish_engulfing(p, c) -> bool:
    o0, _, _, c0 = _ohlc(p)
    o1, _, _, c1 = _ohlc(c)
    return c0 > o0 and c1 < o1 and o1 >= c0 and c1 <= o0 and _body(o1, c1) > _body(o0, c0)


def _morning_star(a, b, c) -> bool:
    o1, h1, l1, c1 = _ohlc(a)
    o2, _, _, c2 = _ohlc(b)
    o3, _, _, c3 = _ohlc(c)
    if c1 >= o1:
        return False
    body1 = o1 - c1
    if body1 / _rng(h1, l1) < 0.45:
        return False
    if _body(o2, c2) > body1 * 0.5:
        return False
    if min(o2, c2) > (o1 + c1) / 2:
        return False
    return c3 > o3 and c3 > (o1 + c1) / 2


def _evening_star(a, b, c) -> bool:
    o1, h1, l1, c1 = _ohlc(a)
    o2, _, _, c2 = _ohlc(b)
    o3, _, _, c3 = _ohlc(c)
    if c1 <= o1:
        return False
    body1 = c1 - o1
    if body1 / _rng(h1, l1) < 0.45:
        return False
    if _body(o2, c2) > body1 * 0.5:
        return False
    if max(o2, c2) < (o1 + c1) / 2:
        return False
    return c3 < o3 and c3 < (o1 + c1) / 2


def _three_white_soldiers(a, b, c) -> bool:
    rows = [a, b, c]
    closes = []
    for r in rows:
        o, h, l, cl = _ohlc(r)
        if cl <= o or _body(o, cl) / _rng(h, l) < 0.45:
            return False
        closes.append(cl)
    return closes[0] < closes[1] < closes[2]


def _three_black_crows(a, b, c) -> bool:
    rows = [a, b, c]
    closes = []
    for r in rows:
        o, h, l, cl = _ohlc(r)
        if cl >= o or _body(o, cl) / _rng(h, l) < 0.45:
            return False
        closes.append(cl)
    return closes[0] > closes[1] > closes[2]


def _avg_body(df: pd.DataFrame, n: int = 14) -> float:
    body = (pd.to_numeric(df["close"], errors="coerce") - pd.to_numeric(df["open"], errors="coerce")).abs()
    v = float(body.tail(n).mean())
    return v if np.isfinite(v) and v > 0 else 0.0


def detect_patterns(df: pd.DataFrame, lookback: int = 1) -> list[CandlePattern]:
    """Patterns that complete within the last ``lookback`` bars (default: last bar only)."""
    need = ["open", "high", "low", "close"]
    if df is None or len(df) < 3 or any(c not in df.columns for c in need):
        return []
    d = df.tail(max(20, lookback + 5)).reset_index(drop=True)
    avg_body = _avg_body(d)
    found: list[CandlePattern] = []
    last_i = len(d) - 1
    start = max(2, last_i - lookback + 1)
    for i in range(start, last_i + 1):
        cur = d.iloc[i]
        prev = d.iloc[i - 1]
        prev2 = d.iloc[i - 2]
        o, h, l, c = _ohlc(cur)
        if _is_doji(o, h, l, c):
            found.append(CandlePattern("十字星", "single", "neutral", 1))
        if _is_hammer(o, h, l, c):
            found.append(CandlePattern("锤子线", "single", "bull", 1))
        if _is_shooting_star(o, h, l, c):
            found.append(CandlePattern("射击之星", "single", "bear", 1))
        if _is_long_yang(o, h, l, c, avg_body):
            found.append(CandlePattern("大阳线", "single", "bull", 1))
        if _is_long_yin(o, h, l, c, avg_body):
            found.append(CandlePattern("大阴线", "single", "bear", 1))
        if _bullish_engulfing(prev, cur):
            found.append(CandlePattern("看涨吞没", "double", "bull", 2))
        if _bearish_engulfing(prev, cur):
            found.append(CandlePattern("看跌吞没", "double", "bear", 2))
        if _morning_star(prev2, prev, cur):
            found.append(CandlePattern("晨星", "triple", "bull", 3))
        if _evening_star(prev2, prev, cur):
            found.append(CandlePattern("暮星", "triple", "bear", 3))
        if _three_white_soldiers(prev2, prev, cur):
            found.append(CandlePattern("三白兵", "triple", "bull", 3))
        if _three_black_crows(prev2, prev, cur):
            found.append(CandlePattern("三黑鸦", "triple", "bear", 3))
    # Prefer unique names, last occurrence wins order.
    uniq: dict[str, CandlePattern] = {}
    for p in found:
        uniq[p.name] = p
    return list(uniq.values())


def recent_pattern_names(df: pd.DataFrame) -> list[str]:
    return [p.name for p in detect_patterns(df, lookback=1)]


def pattern_score(df: Optional[pd.DataFrame] = None) -> float:
    """Map recent candles onto Opportunity Score ``similar_pattern`` (0-100, default 50)."""
    if df is None or len(df) < 3:
        return 50.0
    patterns = detect_patterns(df, lookback=1)
    if not patterns:
        return 50.0
    delta = 0.0
    close = pd.to_numeric(df["close"], errors="coerce")
    cur = float(close.iloc[-1])
    lo = float(close.tail(60).min())
    hi = float(close.tail(60).max())
    span = hi - lo if hi > lo else 1.0
    pos = (cur - lo) / span
    for p in patterns:
        dlt = float(_PATTERN_DELTA.get(p.name, 0))
        if p.name == "十字星":
            dlt = 6.0 if pos <= 0.35 else (-6.0 if pos >= 0.75 else 0.0)
        if p.direction == "bull" and pos <= 0.35:
            dlt += 6.0
        if p.direction == "bear" and pos >= 0.75:
            dlt -= 6.0
        delta += dlt
    return float(np.clip(50.0 + delta, 0, 100))
