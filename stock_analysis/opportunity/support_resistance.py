"""支撑 / 阻力引擎。

用「近 N 日高低点聚类 + 均线 + 布林 + 筹码密集区」识别关键价位：
  * 支撑：局部低点聚类、MA20/MA60、布林下轨、成交密集区下沿
  * 阻力：局部高点聚类、MA20/MA60（位于价格上方时）、布林上轨、前高

输出保留原始证据，供 AI 解读与交易计划引用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class SupportResistance:
    """支撑/阻力检测结果。价格均为绝对价位（元）。"""

    supports: list[float] = field(default_factory=list)
    resistances: list[float] = field(default_factory=list)
    key_support: Optional[float] = None
    key_resistance: Optional[float] = None
    # 证据：每个价位对应来源说明
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "supports": self.supports,
            "resistances": self.resistances,
            "key_support": self.key_support,
            "key_resistance": self.key_resistance,
            "evidence": self.evidence,
        }


def _cluster_levels(points: list[float], tol_pct: float = 1.5) -> list[float]:
    """把相近价位聚成一簇，返回簇中心（按价格升序）。

    tol_pct: 簇内最大相对跨度（%）。过近的价位合并，避免重复支撑/阻力。
    """
    if not points:
        return []
    pts = sorted(points)
    clusters: list[list[float]] = [[pts[0]]]
    for p in pts[1:]:
        if (p - clusters[-1][0]) / clusters[-1][0] * 100 <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [float(np.mean(c)) for c in clusters]


def detect_support_resistance(
    df: pd.DataFrame,
    lookback: int = 60,
    swing_window: int = 5,
    tol_pct: float = 1.5,
    require_volume: bool = False,
) -> SupportResistance:
    """从日K（需含 high/low/close/volume，建议先 add_all_indicators）识别支撑/阻力。

    Args:
        df: 至少含 high/low/close/volume 的 DataFrame；若含 ma20/ma60/boll_* 则叠加均线与布林证据。
        lookback: 回看窗口（交易日）。
        swing_window: 摆动点判定窗口（局部极值两侧各 N 根）。
        tol_pct: 聚类容差（%）。
        require_volume: 是否要求成交密集区（筹码）参与证据。
    """
    if df is None or df.empty or len(df) < swing_window * 2 + 5:
        return SupportResistance()

    d = df.tail(lookback).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce").astype(float)
    high = pd.to_numeric(d["high"], errors="coerce").astype(float)
    low = pd.to_numeric(d["low"], errors="coerce").astype(float)

    # 1) 摆动高低点
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    n = len(d)
    for i in range(swing_window, n - swing_window):
        win_hi = high.iloc[i - swing_window : i + swing_window + 1]
        win_lo = low.iloc[i - swing_window : i + swing_window + 1]
        if high.iloc[i] == win_hi.max():
            swing_highs.append(float(high.iloc[i]))
        if low.iloc[i] == win_lo.min():
            swing_lows.append(float(low.iloc[i]))

    # 2) 均线（MA20/MA60）作为动态支撑/阻力
    ma_ev: list[tuple[float, str]] = []
    for col, name in (("ma20", "MA20"), ("ma60", "MA60")):
        if col in d.columns:
            v = pd.to_numeric(d[col], errors="coerce").iloc[-1]
            if v is not None and not np.isnan(v):
                ma_ev.append((float(v), name))

    # 3) 布林轨道
    boll_ev: list[tuple[float, str]] = []
    for col, name in (("boll_upper", "BOLL上轨"), ("boll_lower", "BOLL下轨"), ("boll_mid", "BOLL中轨")):
        if col in d.columns:
            v = pd.to_numeric(d[col], errors="coerce").iloc[-1]
            if v is not None and not np.isnan(v):
                boll_ev.append((float(v), name))

    cur = float(close.iloc[-1])
    evidence: dict = {}

    # 支撑候选：摆动低点 + 低于现价的均线/布林
    sup_points = list(swing_lows)
    for v, name in ma_ev + boll_ev:
        if v < cur:
            sup_points.append(v)
    supports = _cluster_levels([p for p in sup_points if p < cur * 1.02], tol_pct)
    # 只保留现价下方、且相距至少 0.3% 的支撑
    supports = [s for s in supports if s < cur * 0.997]

    # 阻力候选：摆动高点 + 高于现价的均线/布林 + 前高
    res_points = list(swing_highs)
    for v, name in ma_ev + boll_ev:
        if v > cur:
            res_points.append(v)
    resistances = _cluster_levels([p for p in res_points if p > cur * 0.98], tol_pct)
    resistances = [r for r in resistances if r > cur * 1.003]

    # 成交密集区（可选筹码证据）：近 20 日成交量加权价位的峰
    if require_volume and "volume" in d.columns and len(d) >= 20:
        vol = pd.to_numeric(d["volume"], errors="coerce").astype(float)
        vwap20 = float(np.average((high + low + close).iloc[-20:] / 3, weights=vol.iloc[-20:]))
        if vwap20 < cur:
            supports.append(vwap20)
            evidence.setdefault("volume_dense", [round(vwap20, 2)])
        elif vwap20 > cur:
            resistances.append(vwap20)
            evidence.setdefault("volume_dense", [round(vwap20, 2)])
        supports = _cluster_levels(supports, tol_pct)
        resistances = _cluster_levels(resistances, tol_pct)

    # 关键支撑/阻力：与现价最近的有效价位
    key_support = max(supports) if supports else None
    key_resistance = min(resistances) if resistances else None

    # 证据整理
    if key_support is not None:
        src = []
        if key_support in swing_lows:
            src.append("摆动低点")
        for v, name in ma_ev + boll_ev:
            if abs(v - key_support) / cur * 100 < 1.0:
                src.append(name)
        evidence["key_support"] = {"level": round(key_support, 2), "sources": src or ["聚类支撑"]}
    if key_resistance is not None:
        src = []
        if key_resistance in swing_highs:
            src.append("摆动高点")
        for v, name in ma_ev + boll_ev:
            if abs(v - key_resistance) / cur * 100 < 1.0:
                src.append(name)
        evidence["key_resistance"] = {"level": round(key_resistance, 2), "sources": src or ["聚类阻力"]}

    return SupportResistance(
        supports=[round(s, 2) for s in supports],
        resistances=[round(r, 2) for r in resistances],
        key_support=round(key_support, 2) if key_support else None,
        key_resistance=round(key_resistance, 2) if key_resistance else None,
        evidence=evidence,
    )
