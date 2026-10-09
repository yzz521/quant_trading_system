"""交易计划（TradingPlan）—— V2 核心产物。

统一表达：决策状态、个股评分、机会评分、入场区间、止损、三档目标价、
风险收益比、建议仓位、持有周期、置信度。供 AI 分析师与 Dashboard 直接消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .entry_price import EntryPrice
    from .exit_price import ExitPrice
    from .quality_gate import QualityGateResult
    from .risk_reward import RiskReward


class DecisionState(str, Enum):
    """决策状态（计划书 §11）。"""

    BUY_NOW = "BUY_NOW"                 # 现价已进入合理入场区间
    BUY_ON_PULLBACK = "BUY_ON_PULLBACK" # 股好但现价偏高，等回踩
    WATCH = "WATCH"                     # 值得关注但条件未满足
    HOLD = "HOLD"                       # 已持有，继续跟踪
    SELL = "SELL"                       # 达到目标或逻辑失效
    AVOID = "AVOID"                     # 风险过高，不进候选池

    @property
    def emoji(self) -> str:
        return {
            DecisionState.BUY_NOW: "🟢",
            DecisionState.BUY_ON_PULLBACK: "🟢",
            DecisionState.WATCH: "🟡",
            DecisionState.HOLD: "🟠",
            DecisionState.SELL: "🔴",
            DecisionState.AVOID: "⛔",
        }[self]


@dataclass
class TradingPlan:
    """一份完整的交易计划。"""

    code: str = ""
    name: str = ""
    decision: DecisionState = DecisionState.WATCH

    stock_score: Optional[float] = None
    opportunity_score: Optional[float] = None

    current_price: Optional[float] = None

    entry_low: Optional[float] = None
    entry_price: Optional[float] = None
    entry_high: Optional[float] = None

    stop_loss: Optional[float] = None

    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None

    risk_reward_1: Optional[float] = None
    risk_reward_2: Optional[float] = None

    position_percent: Optional[float] = None
    holding_period: str = "5~20 个交易日"
    confidence: Optional[float] = None

    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    invalidate_condition: str = ""

    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "decision": self.decision.value,
            "decision_emoji": self.decision.emoji,
            "stock_score": self.stock_score,
            "opportunity_score": self.opportunity_score,
            "current_price": self.current_price,
            "entry_low": self.entry_low,
            "entry_price": self.entry_price,
            "entry_high": self.entry_high,
            "stop_loss": self.stop_loss,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "risk_reward_1": self.risk_reward_1,
            "risk_reward_2": self.risk_reward_2,
            "position_percent": self.position_percent,
            "holding_period": self.holding_period,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "risks": self.risks,
            "invalidate_condition": self.invalidate_condition,
            "meta": self.meta,
        }


def build_trading_plan(
    *,
    code: str,
    name: str,
    current_price: float,
    entry: "EntryPrice",
    exit_: "ExitPrice",
    rr: "RiskReward",
    stock_score: float,
    opportunity_score: float,
    position_percent: Optional[float] = None,
    confidence: Optional[float] = None,
    reasons: Optional[list[str]] = None,
    risks: Optional[list[str]] = None,
    invalidate_condition: str = "",
    holding_period: str = "5~20 个交易日",
    gate: Optional["QualityGateResult"] = None,
    price_as_of: str = "",
    bar_as_of: str = "",
    fundamental_as_of: str = "",
    data_coverage: Optional[float] = None,
    geometry_ok: bool = True,
    geometry_note: str = "",
) -> TradingPlan:
    """由各引擎输出组装 TradingPlan 并自动判定决策状态。

    决策规则（优先级从高到低）：
      1. 几何不自洽（``geometry_ok=False``） → AVOID
      2. RR < 1.5              → AVOID
      3. 现价 ≤ 入场区间上沿   → BUY_NOW
      4. 现价 ≤ 理想入场*1.15  → BUY_ON_PULLBACK（接近区间）
      5. 否则                  → WATCH

    **几何自洽性（2026-10 新增）**：入场区间与止损/目标矛盾的计划直接作废 ——
    例如区间下沿低于止损（「在止损位下方买入」）或上沿高于目标（「买在目标价上方」）。
    这类计划即使分数与赔率都好看，也是字面意义上无法执行的。

    **质量闸门（2026-09 新增）**：传入 ``gate`` 后，买入状态还要过质量关：

      * ``gate.tier == REJECT`` → 直接 AVOID（重大风险 / 亏损股 / 估值离谱 / 流动性不足）
      * ``gate.tier == WATCH``  → 买入状态降级为 WATCH（分数不够 / 数据不全 / RR 偏低）
      * ``gate.tier == BUY``    → 保持原决策

    降级而不是直接丢弃，是为了让用户看到「它本来会被推荐，但没通过哪一条」——
    不通过原因写入 ``meta["quality_gate"]``，看板与 AI 解读直接展示。
    """
    decision = DecisionState.WATCH
    low = entry.low if entry else None
    high = entry.high if entry else None
    ideal = entry.ideal if entry else None
    risks = list(risks or [])

    if not geometry_ok:
        decision = DecisionState.AVOID
        if geometry_note:
            line = f"计划几何不自洽：{geometry_note}"
            if line not in risks:
                risks.append(line)
    elif rr.ratio_1 is not None and rr.ratio_1 < 1.5:
        decision = DecisionState.AVOID
    elif low is not None and high is not None:
        if current_price <= high:
            decision = DecisionState.BUY_NOW
        elif ideal is not None and current_price <= ideal * 1.15:
            decision = DecisionState.BUY_ON_PULLBACK
        else:
            decision = DecisionState.WATCH

    # ---- 质量闸门：买入状态必须过质量关 ----
    gate_downgrade = ""
    if gate is not None and not gate.passed:
        if gate.tier == "REJECT":
            if decision != DecisionState.AVOID:
                gate_downgrade = f"{decision.value} → AVOID（质量闸门硬否决）"
            decision = DecisionState.AVOID
        elif decision in (DecisionState.BUY_NOW, DecisionState.BUY_ON_PULLBACK):
            gate_downgrade = f"{decision.value} → WATCH（质量闸门未通过）"
            decision = DecisionState.WATCH

    meta: dict = {
        "stop_source": exit_.stop_source if exit_ else "",
        "grade": rr.grade if rr else "",
    }
    if gate is not None:
        meta["quality_gate"] = gate.to_dict()
        if gate_downgrade:
            meta["gate_downgrade"] = gate_downgrade
    if data_coverage is not None:
        meta["data_coverage"] = round(float(data_coverage), 3)
    if price_as_of:
        meta["price_as_of"] = price_as_of
    if bar_as_of:
        meta["bar_as_of"] = bar_as_of
    if fundamental_as_of:
        meta["fundamental_as_of"] = fundamental_as_of
    if geometry_note:
        meta["geometry_note"] = geometry_note
    if not geometry_ok:
        meta["geometry_ok"] = False

    return TradingPlan(
        code=code,
        name=name,
        decision=decision,
        stock_score=round(stock_score, 1),
        opportunity_score=round(opportunity_score, 1),
        current_price=round(current_price, 2),
        entry_low=low,
        entry_price=entry.standard if entry else None,
        entry_high=high,
        stop_loss=exit_.stop_loss if exit_ else None,
        target_1=exit_.target_1 if exit_ else None,
        target_2=exit_.target_2 if exit_ else None,
        target_3=exit_.target_3 if exit_ else None,
        risk_reward_1=rr.ratio_1 if rr else None,
        risk_reward_2=rr.ratio_2 if rr else None,
        position_percent=position_percent,
        holding_period=holding_period,
        confidence=confidence,
        reasons=reasons or [],
        risks=risks,
        invalidate_condition=invalidate_condition,
        meta=meta,
    )
