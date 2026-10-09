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

#: 止损距离不得小于 ``n × ATR``。
#:
#: 原实现 ``stop = max(candidates)`` 取「最接近现价」的一档，即**最紧**的止损。
#: 这看起来最保守（每股亏损最小），实际是把止损塞进日内噪声里 —— 一天的平均波动
#: （ATR）就能扫掉它，而赔率因为分母被压小反而显得漂亮。审计实测「高 RR 组
#: 97.1% 止损离场」正是这个症状。取 0.5×ATR 作为下限：仍允许比固定 7% 更紧，
#: 但不再紧到「一个日内波动就出局」。
MIN_STOP_ATR_MULT = 0.5


#: 第一目标价相对入场价的波动率上限（``entry + n × ATR``）。
#:
#: ``t1`` 的候选里有 ``prev_high``（近 120 日最高价）。对一只已从高位跌下来的股票，
#: 这个"前高"可以远在现价之上 —— 实测华工科技（现价 86.99）的 T1 被设成 187.66
#: （+108%，≈18×ATR），立讯精密 +66.8%（≈16×ATR）。这类目标不是"第一目标"，
#: 而是长期阻力位；它唯一的效果是把 RR 抬到 15.42 / 10.49，让计划**白过**
#: ``min_rr=2.0`` 的闸门 —— 与「止损贴着入场价」是同一个套路的镜像。
#:
#: 取 6 的理由：计划书写明持有期「5~20 个交易日」，随机游走下 20 日的期望位移
#: ≈ ``ATR × √20 ≈ 4.5×ATR``，6 倍留约 1.3 倍余量。按波动率缩放，而不是拍一个
#: 固定百分比 —— 高波动票本来就该允许更远的绝对目标。
#: 实测该阈值恰好只拦掉上述 2 只病态样本，对其余 38 只（最高 4.5×ATR）零影响。
MAX_T1_ATR_MULT = 6.0


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
    min_stop_atr_mult: float = MIN_STOP_ATR_MULT,
    max_t1_atr_mult: float = MAX_T1_ATR_MULT,
) -> ExitPrice:
    """计算止损与三档目标价。

    Args:
        df: 已加指标的日K。
        entry_price: 参考入场价（未传则用现价）。
        sr: 支撑/阻力结果。
        atr_mult_stop: ATR 止损倍数（ATR 止损 = 现价 - n*ATR）。
        fixed_risk_pct: 固定风险止损比例（相对于入场价）。
        min_stop_atr_mult: 止损距离的 ATR 倍数下限（0 表示不启用）。
            宽度上限受 ``fixed_risk_pct`` 约束 —— 不会因为 ATR 很大就把风险
            放大到超过固定风险比例。
        max_t1_atr_mult: 第一目标距入场价的 ATR 倍数上限（0 表示不启用）。
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
    # 取最高（最接近现价 → 每股亏损最小）的一档，再向下取整到分
    stop_raw, stop_src = max(candidates, key=lambda c: c[0])
    # 止损距离下限：不得小于 min_stop_atr_mult × ATR（否则止损落在日内噪声里）。
    # 用 max(..., 固定风险止损) 兜住 —— 不允许 ATR 很大时把风险撑过 fixed_risk_pct。
    if atr and atr > 0 and min_stop_atr_mult > 0:
        floor_stop = max(entry - min_stop_atr_mult * atr, entry * (1 - fixed_risk_pct))
        if stop_raw > floor_stop:
            stop_raw = floor_stop
            stop_src = f"ATR下限({min_stop_atr_mult:g}×ATR)"
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
    # 目标必须落在「持有期内可能走到」的范围内（按波动率缩放，见 MAX_T1_ATR_MULT）
    if atr and atr > 0 and max_t1_atr_mult > 0:
        cap = entry + max_t1_atr_mult * atr
        if t1 > cap:
            t1 = cap
            t1_src = f"{t1_src}（受 {max_t1_atr_mult:g}×ATR 上限约束）"
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
