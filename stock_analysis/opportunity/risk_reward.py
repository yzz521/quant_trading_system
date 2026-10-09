"""风险收益比（Risk/Reward）与评估。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 止损距离下限（占入场价比例）。低于此值**赔率不可信**，不是「高赔率机会」。
#:
#: 理由：
#:   1. A 股双边交易成本约 0.20%（佣金+过户费+滑点，见 ``backtest.execution.CostModel``），
#:      小额订单触及最低佣金后可达 0.39%。**止损距离比成本还近意味着价格不动也在亏。**
#:   2. 更隐蔽的是**数学放大**：RR = (目标-入场)/(入场-止损)，分母趋近 0 时 RR 会
#:      被放大成几十上百倍 —— 实测批量扫描出现过止损距离 0.01%、risk_reward_1=180
#:      的计划（五粮液）；历史样本里 RR 的 P99=287、高 RR 组 97.1% 止损离场。
#:   3. A 股日 ATR 中位约 2%~3%，止损距离小于 1% 基本落在**单日噪声**里，
#:      开盘一个跳空就被扫掉，与「逻辑失效才离场」无关。
#:
#: 取值 1.0% ≈ 2.6× 基础成本、≈ 5× 触底佣金情形 —— 比成本线留出足够余量，
#: 又远低于实测止损距离中位数（6.36%），只拦掉尾部病态样本。
#:
#: 历史注：本常量上一版取 0.3%，**低于本注释自己引用的 0.387% 成本线** ——
#: 自相矛盾，属实现与理由不符，已修正。
MIN_RISK_PCT = 0.01


@dataclass
class RiskReward:
    """风险收益比计算结果。"""

    risk: Optional[float] = None      # 每股价差风险（元）
    reward_1: Optional[float] = None  # 到 T1 的收益（元）
    reward_2: Optional[float] = None  # 到 T2 的收益（元）
    ratio_1: Optional[float] = None   # R/R（T1）
    ratio_2: Optional[float] = None   # R/R（T2）
    grade: str = ""
    tradable: bool = True             # 止损距离是否够远（够远才谈得上「赔率」）
    note: str = ""                    # 不可交易 / 无效时的原因

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "reward_1": self.reward_1,
            "reward_2": self.reward_2,
            "ratio_1": self.ratio_1,
            "ratio_2": self.ratio_2,
            "grade": self.grade,
            "tradable": self.tradable,
            "note": self.note,
        }


def _grade(ratio: Optional[float]) -> str:
    if ratio is None:
        return ""
    if ratio < 1.5:
        return "不推荐"
    if ratio < 2.0:
        return "可观察"
    if ratio < 3.0:
        return "良好"
    return "优秀"


def calc_risk_reward(
    entry: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    *,
    min_risk_pct: float = MIN_RISK_PCT,
) -> RiskReward:
    """按计划书：Risk = entry - stop；Reward = target - entry。

    止损距离低于 ``min_risk_pct`` 时返回 ``ratio_1/ratio_2 = None`` 且
    ``tradable=False`` —— 宁可说「评估不了」，也不给出被数学放大的假赔率。
    下游闸门会把「无赔率」按不通过处理（见 ``quality_gate.evaluate``）。
    """
    if not all(v is not None and v > 0 for v in (entry, stop_loss, target_1, target_2)):
        return RiskReward(tradable=False, note="入场/止损/目标参数不全，无法计算赔率")
    risk = entry - stop_loss
    if risk <= 0:
        return RiskReward(tradable=False, note="止损不低于入场价，计划无效")
    risk_pct = risk / entry
    if min_risk_pct and risk_pct < min_risk_pct:
        return RiskReward(
            risk=round(risk, 2),
            reward_1=round(target_1 - entry, 2),
            reward_2=round(target_2 - entry, 2),
            tradable=False,
            note=(f"止损距离 {risk_pct * 100:.2f}% < 下限 {min_risk_pct * 100:.1f}%"
                  f"（低于交易成本，开仓即亏；赔率不可信）"),
        )
    reward_1 = target_1 - entry
    reward_2 = target_2 - entry
    ratio_1 = round(reward_1 / risk, 2)
    ratio_2 = round(reward_2 / risk, 2)
    return RiskReward(
        risk=round(risk, 2),
        reward_1=round(reward_1, 2),
        reward_2=round(reward_2, 2),
        ratio_1=ratio_1,
        ratio_2=ratio_2,
        grade=_grade(ratio_1),
    )
