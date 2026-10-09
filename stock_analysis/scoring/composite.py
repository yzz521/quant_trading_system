"""组合分 —— 把「质量 / 时机 / 赔率」合成一个可排序的分数。

背景
----
原实现按 ``opportunity_score``（机会分）单独排序。机会分的 7 个成分里，现价位置、
支撑强度、趋势、距入场距离、波动率、相似形态合计占 80%，风险收益比占 20% ——
**没有一项与公司质量有关**。于是「基本面差但技术形态漂亮」的票会排在最前面。

组合分把三个维度显式拆开：

    质量 45%  ← stock_score（9 因子，含基本面/成长/估值/风险）
    时机 35%  ← opportunity_score（价位与形态）
    赔率 20%  ← risk_reward_1（映射到 0-100，4R 封顶）

权重是**可配置的默认值**，不是经过样本外标定的最优解 —— 标定必须走
``stock_analysis.research.factor_validation`` 的滚动样本外流程，不能靠拍脑袋。
因此这里把权重放在显式常量里，方便日后用数据替换。
"""
from __future__ import annotations

from typing import Optional

# 质量 / 时机 / 赔率 的默认权重（和 = 1.0）
COMPOSITE_WEIGHTS: dict[str, float] = {
    "stock": 0.45,
    "opportunity": 0.35,
    "rr": 0.20,
}

# 赔率映射上限：RR ≥ 4 视为满分，避免极端 RR 把组合分整体带偏
RR_CAP = 4.0


def _num(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def rr_component(risk_reward_1: Optional[float], cap: float = RR_CAP) -> float:
    """把风险收益比映射到 0-100（``cap`` 封顶，负值归 0）。"""
    v = _num(risk_reward_1)
    if v is None or v <= 0:
        return 0.0
    return max(0.0, min(100.0, v / cap * 100.0))


def composite_score(
    *,
    stock_score: Optional[float],
    opportunity_score: Optional[float],
    risk_reward_1: Optional[float],
    weights: Optional[dict] = None,
) -> float:
    """组合分（0-100）。缺失维度按 0 计（而非中性 50）—— 缺失不应被奖励。"""
    w = {**COMPOSITE_WEIGHTS, **(weights or {})}
    s = _num(stock_score) or 0.0
    o = _num(opportunity_score) or 0.0
    r = rr_component(risk_reward_1)
    return (
        max(0.0, min(100.0, s)) * w["stock"]
        + max(0.0, min(100.0, o)) * w["opportunity"]
        + r * w["rr"]
    )


def score_plan(plan: Optional[dict], weights: Optional[dict] = None) -> float:
    """对 TradingPlan 的 dict 形式计算组合分（plan 为 None 时返回 0）。"""
    if not plan:
        return 0.0
    return composite_score(
        stock_score=plan.get("stock_score"),
        opportunity_score=plan.get("opportunity_score"),
        risk_reward_1=plan.get("risk_reward_1"),
        weights=weights,
    )
