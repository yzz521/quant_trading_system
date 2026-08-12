"""入场价格引擎 —— 计算理想/标准/激进三档入场价与入场区间。

不能简单用固定百分比，必须综合：现价、均线、前高前低、支撑/阻力、ATR、
布林、量能、突破位、成交密集区。输出一个「入场区间」而非单一价格。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .support_resistance import SupportResistance, detect_support_resistance


@dataclass
class EntryPrice:
    """入场价格计算结果。"""

    ideal: Optional[float] = None      # 理想入场（回调较深）
    standard: Optional[float] = None   # 标准入场（默认参考）
    aggressive: Optional[float] = None # 激进入场（突破/贴现价）
    low: Optional[float] = None        # 入场区间下沿
    high: Optional[float] = None       # 入场区间上沿
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ideal": self.ideal,
            "standard": self.standard,
            "aggressive": self.aggressive,
            "low": self.low,
            "high": self.high,
            "evidence": self.evidence,
        }


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def calc_entry_zone(
    df: pd.DataFrame,
    sr: Optional[SupportResistance] = None,
    atr_mult_buy: float = 0.5,
) -> EntryPrice:
    """计算入场区间。

    Args:
        df: 已加指标的日K（需 close/ma5/ma20/ma60/atr/boll_*，含 high/low/volume 更好）。
        sr: 预计算的支撑/阻力；None 时内部自动计算。
        atr_mult_buy: 以 ATR 倍数定义标准入场回调深度。
    """
    if df is None or df.empty:
        return EntryPrice()
    d = df.tail(120).reset_index(drop=True)
    cur = _num(d["close"].iloc[-1])
    if cur is None:
        return EntryPrice()

    if sr is None:
        sr = detect_support_resistance(d)

    ma5 = _num(d["ma5"].iloc[-1]) if "ma5" in d.columns else None
    ma20 = _num(d["ma20"].iloc[-1]) if "ma20" in d.columns else None
    ma60 = _num(d["ma60"].iloc[-1]) if "ma60" in d.columns else None
    atr = _num(d["atr"].iloc[-1]) if "atr" in d.columns else None
    boll_low = _num(d["boll_lower"].iloc[-1]) if "boll_lower" in d.columns else None
    boll_mid = _num(d["boll_mid"].iloc[-1]) if "boll_mid" in d.columns else None

    # 前高/前低（排除最后一日，避免用当日高低做突破依据）
    prev_high = _num(d["high"].iloc[:-1].max()) if len(d) > 1 and "high" in d.columns else None
    prev_low = _num(d["low"].iloc[:-1].min()) if len(d) > 1 and "low" in d.columns else None

    evidence: dict = {}

    # 候选基准位：支撑（最近的关键支撑）、MA20、MA60、布林中轨
    bases: list[tuple[float, str]] = []
    if sr and sr.key_support is not None and sr.key_support < cur:
        bases.append((sr.key_support, "关键支撑"))
    if ma20 and ma20 < cur:
        bases.append((ma20, "MA20"))
    if ma60 and ma60 < cur:
        bases.append((ma60, "MA60"))
    if boll_mid and boll_mid < cur:
        bases.append((boll_mid, "BOLL中轨"))
    if prev_low:
        bases.append((prev_low, "近期低点"))

    if not bases:
        # 无下方参考时，用 ATR 估算回调空间
        atr_v = atr or cur * 0.03
        bases.append((cur - atr_v, "ATR估算"))

    # 标准入场 = 最近的支撑/均线基准（取最接近现价的回调位）
    standard_base, standard_src = max(bases, key=lambda b: b[0])
    standard = standard_base
    evidence["standard"] = {"price": round(standard, 2), "source": standard_src}

    # 理想入场 = 更深一档回调（更下方的基准或再减 0.5*ATR）
    deeper = [b for b in bases if b[0] < standard_base - (atr or cur * 0.02) * 0.3]
    if deeper:
        ideal_base, ideal_src = max(deeper, key=lambda b: b[0])
        ideal = ideal_base
        evidence["ideal"] = {"price": round(ideal, 2), "source": ideal_src}
    else:
        ideal = standard - (atr or cur * 0.03) * 0.5
        evidence["ideal"] = {"price": round(ideal, 2), "source": "标准回调加深0.5ATR"}

    # 激进入场 = 贴现价 / 突破位（现价 0.3%~0.8% 内，或 MA5）
    aggr_candidates = []
    if ma5 and ma5 > cur:
        aggr_candidates.append((ma5, "MA5"))
    if prev_high and prev_high > cur:
        aggr_candidates.append((prev_high, "前高突破"))
    if boll_low and boll_low > cur:
        aggr_candidates.append((boll_low, "BOLL下轨"))
    if aggr_candidates:
        aggressive, aggr_src = min(aggr_candidates, key=lambda b: b[0])
    else:
        aggressive = cur * 1.005
        aggr_src = "贴现价0.5%"
    # 激进入场不应比现价低很多（否则就不是激进了）
    if aggressive < cur * 0.99:
        aggressive = cur * 1.002
        aggr_src = "贴现价0.2%"
    evidence["aggressive"] = {"price": round(aggressive, 2), "source": aggr_src}

    # 入场区间
    low = min(ideal, standard)
    high = aggressive
    if high <= low:
        high = low * 1.02
    evidence["low"] = round(low, 2)
    evidence["high"] = round(high, 2)

    return EntryPrice(
        ideal=round(ideal, 2),
        standard=round(standard, 2),
        aggressive=round(aggressive, 2),
        low=round(low, 2),
        high=round(high, 2),
        evidence=evidence,
    )
