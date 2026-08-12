"""风险收益比（Risk/Reward）与评估。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskReward:
    """风险收益比计算结果。"""

    risk: Optional[float] = None      # 每股价差风险（元）
    reward_1: Optional[float] = None  # 到 T1 的收益（元）
    reward_2: Optional[float] = None  # 到 T2 的收益（元）
    ratio_1: Optional[float] = None   # R/R（T1）
    ratio_2: Optional[float] = None   # R/R（T2）
    grade: str = ""

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "reward_1": self.reward_1,
            "reward_2": self.reward_2,
            "ratio_1": self.ratio_1,
            "ratio_2": self.ratio_2,
            "grade": self.grade,
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


def calc_risk_reward(entry: float, stop_loss: float, target_1: float, target_2: float) -> RiskReward:
    """按计划书：Risk = entry - stop；Reward = target - entry。"""
    if not all(v is not None and v > 0 for v in (entry, stop_loss, target_1, target_2)):
        return RiskReward()
    risk = entry - stop_loss
    reward_1 = target_1 - entry
    reward_2 = target_2 - entry
    if risk <= 0:
        return RiskReward()
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
