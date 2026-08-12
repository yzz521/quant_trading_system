"""止损 + 目标价引擎。

止损来源：结构位（前低）、ATR 止损、支撑位止损、固定风险止损 —— 取最严。
目标价：至少三档（T1/T2/T3），基于前高、阻力、波动率外推，同时输出
expected_return / risk_reward / max_loss。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .support_resistance import SupportResistance, detect_support_resistance


@dataclass
class ExitPrice:
    """止损与目标价计算结果。"""

    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    expected_return: Optional[float] = None   # 以 T1 计算的预期收益率（%）
    risk_reward: Optional[float] = None       # 风险收益比（T1）
    max_loss: Optional[float] = None          # 单笔最大亏损（元/股）
    stop_source: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "stop_loss": self.stop_loss,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "expected_return": self.expected_return,
            "risk_reward": self.risk_reward,
            "max_loss": self.max_loss,
            "stop_source": self.stop_source,
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


def calc_exit_prices(
    df: pd.DataFrame,
    entry_price: Optional[float] = None,
    sr: Optional[SupportResistance] = None,
    atr_mult_stop: float = 1.5,
    fixed_risk_pct: float = 0.07,
) -> ExitPrice:
    """计算止损与三档目标价。

    Args:
        df: 已加指标的日K。
        entry_price: 参考入场价（未传则用现价）。
        sr: 支撑/阻力结果。
        atr_mult_stop: ATR 止损倍数（ATR 止损 = 现价 - n*ATR）。
        fixed_risk_pct: 固定风险止损比例（相对于入场价）。
    """
    if df is None or df.empty:
        return ExitPrice()
    d = df.tail(120).reset_index(drop=True)
    cur = _num(d["close"].iloc[-1])
    if cur is None:
        return ExitPrice()
    entry = entry_price or cur
    atr = _num(d["atr"].iloc[-1]) if "atr" in d.columns else None

    if sr is None:
        sr = detect_support_resistance(d)

    # ---------- 止损：多来源取最严（最高的一档止损 = 风险最小） ----------
    candidates: list[tuple[float, str]] = []
    # 结构止损：近 20 日（不含今日）最低价
    if len(d) > 1 and "low" in d.columns:
        struct = _num(d["low"].iloc[:-1].tail(20).min())
        if struct and struct < entry:
            candidates.append((struct, "结构位(近20日前低)"))
    # ATR 止损
    if atr:
        candidates.append((entry - atr_mult_stop * atr, "ATR止损"))
    # 支撑位止损
    if sr and sr.key_support is not None and sr.key_support < entry:
        candidates.append((sr.key_support, "关键支撑"))
    # 固定风险止损
    candidates.append((entry * (1 - fixed_risk_pct), "固定风险7%"))

    if not candidates:
        return ExitPrice()
    # 取最高（最接近现价 → 最保守）的一档，再向下取整到分
    stop_raw, stop_src = max(candidates, key=lambda c: c[0])
    stop_loss = round(stop_raw, 2)
    # 避免止损 == 入场价
    if stop_loss >= entry:
        stop_loss = round(entry * (1 - fixed_risk_pct), 2)
        stop_src = "固定风险7%"

    # ---------- 目标价：三档 ----------
    # 前高（排除最后一日）
    prev_high = _num(d["high"].iloc[:-1].max()) if len(d) > 1 and "high" in d.columns else None
    # 阻力
    resistance = sr.key_resistance if sr else None

    risk = entry - stop_loss
    if risk <= 0:
        risk = entry * fixed_risk_pct

    # T1: 前高 或 最近阻力 或 2R
    if resistance and resistance > entry:
        t1 = resistance
        t1_src = "关键阻力"
    elif prev_high and prev_high > entry:
        t1 = prev_high
        t1_src = "前高"
    else:
        t1 = entry + 2.0 * risk
        t1_src = "2R目标"
    target_1 = round(t1, 2)

    # T2: 3.5R 或 1.6×T1
    target_2 = round(max(entry + 3.5 * risk, target_1 * 1.1), 2)
    # T3: 5R 或 2×T1（趋势目标，通常结合更大级别阻力）
    target_3 = round(max(entry + 5.0 * risk, target_1 * 1.35), 2)

    expected_return = round((target_1 - entry) / entry * 100, 2) if entry else None
    risk_reward = round((target_1 - entry) / risk, 2) if risk else None
    max_loss = round(entry - stop_loss, 2)

    return ExitPrice(
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
        expected_return=expected_return,
        risk_reward=risk_reward,
        max_loss=max_loss,
        stop_source=stop_src,
        evidence={
            "stop_candidates": {src: round(v, 2) for v, src in candidates},
            "t1_source": t1_src,
            "atr": round(atr, 3) if atr else None,
            "risk": round(risk, 2),
        },
    )
