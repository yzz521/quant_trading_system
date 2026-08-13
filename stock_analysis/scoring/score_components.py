"""共享评分组件 —— 各维度子分（0-100）与归一化工具。

所有子分函数输出 0-100 的分数，由 Stock/Opportunity Score 按权重加权。
数据只依赖日K指标列（add_all_indicators 的输出），不依赖未来数据。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def normalize_component(v: Optional[float], lo: float, hi: float, invert: bool = False) -> float:
    """把 v 线性映射到 0-100，越界截断。invert=True 时越小分越高。"""
    if v is None or np.isnan(v):
        return 50.0
    v = float(v)
    if hi <= lo:
        return 50.0
    r = (v - lo) / (hi - lo)
    if invert:
        r = 1 - r
    return float(np.clip(r * 100, 0, 100))


def score_trend(df: pd.DataFrame) -> float:
    """趋势健康度：MA 多头排列 + 现价与 MA20 相对位置 + MA20 斜率。"""
    if df is None or len(df) < 20:
        return 50.0
    d = df.tail(60).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    ma5 = pd.to_numeric(d["ma5"], errors="coerce") if "ma5" in d.columns else None
    ma20 = pd.to_numeric(d["ma20"], errors="coerce") if "ma20" in d.columns else None
    ma60 = pd.to_numeric(d["ma60"], errors="coerce") if "ma60" in d.columns else None
    if ma20 is None or len(ma20) < 2:
        return 50.0

    cur = float(close.iloc[-1])
    m20 = float(ma20.iloc[-1])
    score = 50.0

    # 多头排列加分
    bull = 0
    if ma5 is not None and not np.isnan(ma5.iloc[-1]):
        if ma5.iloc[-1] > m20:
            bull += 1
    if ma60 is not None and not np.isnan(ma60.iloc[-1]):
        if m20 > ma60.iloc[-1]:
            bull += 1
    if bull == 2:
        score += 20
    elif bull == 1:
        score += 8

    # 现价在 MA20 上方加分，下方减分
    if cur >= m20:
        score += 15 * min(1.0, (cur / m20 - 1) * 50)
    else:
        score -= 20 * min(1.0, (1 - cur / m20) * 40)

    # MA20 斜率（近 10 日）
    slope = (m20 - float(ma20.iloc[-10])) / float(ma20.iloc[-10]) * 100 if len(ma20) >= 10 else 0
    score += float(np.clip(slope * 5, -15, 15))

    return float(np.clip(score, 0, 100))


def score_volume(df: pd.DataFrame) -> float:
    """量能配合：近期量比 + 价量配合。"""
    if df is None or len(df) < 20:
        return 50.0
    d = df.tail(20).reset_index(drop=True)
    vol = pd.to_numeric(d["volume"], errors="coerce")
    close = pd.to_numeric(d["close"], errors="coerce")
    if vol.isna().all():
        return 50.0
    vol_ma = vol.iloc[:-5].mean() if len(vol) > 5 else vol.mean()
    if not vol_ma or np.isnan(vol_ma) or vol_ma <= 0:
        return 50.0
    vol_ratio = float(vol.iloc[-5:].mean() / vol_ma)
    score = normalize_component(vol_ratio, 0.5, 2.5)
    # 温和放量上涨最好（价量同向）
    pct = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) > 1 else 0
    if pct > 0 and vol_ratio > 1.0:
        score = min(100, score + 10)
    elif pct < 0 and vol_ratio > 1.5:
        score = max(0, score - 15)
    return score


def score_volatility(df: pd.DataFrame) -> float:
    """波动率适中打分（过高风险大，过低无弹性）。ATR% 落在 2%~6% 最优。"""
    if df is None or len(df) < 20:
        return 50.0
    d = df.tail(20).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    atr = pd.to_numeric(d["atr"], errors="coerce") if "atr" in d.columns else None
    if atr is None or atr.isna().iloc[-1]:
        return 50.0
    atr_pct = float(atr.iloc[-1] / close.iloc[-1]) * 100
    if atr_pct <= 0:
        return 50.0
    # 2%~6% → 高分，越远越低
    if 2 <= atr_pct <= 6:
        return 90.0
    if atr_pct < 2:
        return normalize_component(atr_pct, 0.5, 2.0)
    return max(0.0, 90.0 - (atr_pct - 6) * 12)


def score_price_position(df: pd.DataFrame, window: int = 60) -> float:
    """现价在近 N 日区间中的位置：过高（追高风险）或过低（弱势）都打折，中部偏上最优。"""
    if df is None or len(df) < 10:
        return 50.0
    d = df.tail(window).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    hi = float(close.max())
    lo = float(close.min())
    cur = float(close.iloc[-1])
    if hi <= lo:
        return 50.0
    pos = (cur - lo) / (hi - lo)  # 0~1
    # 0.3~0.6 最健康；0~0.15（接近新低）与 0.85~1（接近新高）中等；0.15~0.3 偏弱
    if 0.3 <= pos <= 0.7:
        return 85.0
    if 0.15 <= pos < 0.3 or 0.7 < pos <= 0.85:
        return 60.0
    return 40.0


def score_support_strength(df: pd.DataFrame, key_support: Optional[float]) -> float:
    """支撑强度：关键支撑与现价的距离 + 支撑被验证的次数（近 20 日低点触及）。"""
    if df is None or len(df) < 10 or key_support is None:
        return 50.0
    d = df.tail(20).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    low = pd.to_numeric(d["low"], errors="coerce")
    cur = float(close.iloc[-1])
    if cur <= 0 or key_support <= 0:
        return 50.0
    dist_pct = (cur - key_support) / cur * 100  # 距离（%）
    # 距离 1%~5% 为理想（不太近可跌破，不太远回撤大）
    score = normalize_component(dist_pct, 0.5, 6.0)
    # 支撑验证次数：近 20 日低点落在支撑 1.5% 内的次数
    touches = int(((low - key_support).abs() / key_support <= 0.015).sum())
    score += min(20, touches * 5)
    return float(np.clip(score, 0, 100))


def score_rr(ratio: Optional[float]) -> float:
    """风险收益比打分。"""
    if ratio is None:
        return 40.0
    if ratio >= 3.0:
        return 95.0
    if ratio >= 2.0:
        return 75.0
    if ratio >= 1.5:
        return 55.0
    return 25.0


def score_momentum(df: pd.DataFrame) -> float:
    """动量（Momentum）：近 20/60 日收益 + RSI 强度（0-100）。

    与 score_trend（均线结构）互补：趋势看结构、动量看速度。
    复用 roc/rsi 指标列，零新增指标；数据不足返回 50 中性。
    """
    if df is None or len(df) < 21:
        return 50.0
    d = df.tail(61).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    if close.isna().all() or len(close) < 21:
        return 50.0
    cur = float(close.iloc[-1])
    ret20 = (cur / float(close.iloc[-21]) - 1) * 100 if float(close.iloc[-21]) > 0 else 0.0
    ret60 = 0.0
    if len(close) >= 61:
        prev60 = float(close.iloc[0])
        ret60 = (cur / prev60 - 1) * 100 if prev60 > 0 else 0.0
    # RSI 强度：60-75 健康强势，>80 过热，<40 弱
    rsi = pd.to_numeric(d["rsi12"], errors="coerce").iloc[-1] if "rsi12" in d.columns else None
    rsi_score = 50.0
    if rsi is not None and not np.isnan(rsi):
        if 55 <= rsi <= 75:
            rsi_score = 80.0
        elif 45 <= rsi < 55 or 75 < rsi <= 85:
            rsi_score = 60.0
        elif 40 <= rsi < 45:
            rsi_score = 40.0
        else:
            rsi_score = 20.0  # RSI<40 弱 / >85 过热
    score = 0.6 * normalize_component(ret20, -10, 20) + 0.2 * normalize_component(ret60, -15, 30) + 0.2 * rsi_score
    return float(np.clip(score, 0, 100))
