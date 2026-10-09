"""V2 AI 分析师 —— 把量化交易计划翻译成自然语言。

定位（计划书 §16）：AI 不负责决定买卖价格（那是量化引擎的职责），
它负责解释「为什么值得关注 / 能否现在买 / 入场逻辑 / 关键风险 /
失效条件 / 已持有怎么办」。

数据流：TradingPlan(量化结果) → AI → 自然语言解读。

无 AI 配置 / 调用失败时，用规则化兜底文案（不阻塞主流程）。
"""
from __future__ import annotations

import json
from typing import Optional

from ...utils import get_logger
from ..ai_client import cfg_from_notify, chat_completion
from ..opportunity.trading_plan import DecisionState
from .guard import AIReview, DISCLAIMER, ensure_disclaimer, review_ai_text

log = get_logger("AIAnalyst")

_SYSTEM = """你是「AI 分析师」，负责把量化交易计划翻译成投资者能听懂的话。
规则：
1. 只解释量化引擎已算好的事实，**不得出现任何计划里没有的数字**（尤其是买入价、
   止损价、目标价）。引用数值必须逐字来自给定 JSON。
2. 不得改变决策方向：决策为 WATCH/AVOID 时不得出现买入指令；决策为 SELL 时不得
   出现买入或持有指令。
3. 输出必须包含五部分（用 Markdown 小节标题）：
   ## 为什么值得关注
   ## 现在能不能买
   ## 入场逻辑
   ## 关键风险
   ## 失效条件
4. 如果决策是已持有(HOLD)，额外说明持有逻辑。
5. 语言简洁、口语化，总字数控制在 400 字以内。
6. 不承诺收益，不构成投资建议。"""


def _fallback_explain(plan) -> str:
    """无 AI 时的规则化解读（保证主流程可用）。"""
    if plan is None:
        return "（无交易计划）"
    d = plan.decision
    lines = []
    lines.append(f"## 为什么值得关注\n{plan.name or plan.code} 个股评分 {plan.stock_score:.0f}/100、机会评分 {plan.opportunity_score:.0f}/100。")
    if d == DecisionState.AVOID:
        lines.append("## 现在能不能买\n暂不建议，风险收益比不达标。")
    elif d == DecisionState.BUY_NOW:
        lines.append(f"## 现在能不能买\n现价 {plan.current_price} 已进入入场区间 {plan.entry_low}~{plan.entry_high}，符合买入条件。")
    elif d == DecisionState.BUY_ON_PULLBACK:
        lines.append(f"## 现在能不能买\n现价 {plan.current_price} 略高于入场区间 {plan.entry_low}~{plan.entry_high}，建议等回踩。")
    elif d == DecisionState.WATCH:
        lines.append(f"## 现在能不能买\n暂处观察期，现价 {plan.current_price} 与入场区间 {plan.entry_low}~{plan.entry_high} 还有距离。")
    elif d == DecisionState.HOLD:
        lines.append(f"## 现在能不能买\n已持有状态，关注 {plan.stop_loss} 止损位与 {plan.target_1} 目标位。")
    elif d == DecisionState.SELL:
        lines.append("## 现在能不能买\n已触发卖出条件，建议按计划减仓/离场。")

    if plan.entry_price is not None:
        lines.append(f"## 入场逻辑\n标准入场 {plan.entry_price}，区间 {plan.entry_low}~{plan.entry_high}；止损 {plan.stop_loss}，"
                     f"目标 {plan.target_1} / {plan.target_2} / {plan.target_3}，风险收益比 1:{plan.risk_reward_1}。")
    else:
        lines.append("## 入场逻辑\n当前不具备清晰入场条件。")

    lines.append("## 关键风险\n" + ("；".join(plan.risks) if plan.risks else "跌破止损位需严格离场。"))
    lines.append(f"## 失效条件\n{plan.invalidate_condition or '止损位被有效跌破即逻辑失效。'}")
    return "\n\n".join(lines)


def explain_plan(plan, notify_cfg: Optional[dict] = None) -> str:
    """把 TradingPlan 解读为自然语言。返回 Markdown 文本（永不抛异常）。

    所有输出（含规则化兜底）都经过 ``guard`` 校验并附加免责声明：AI 文本若编造
    价格或与决策矛盾，会被丢弃并退回规则化解读——宁可少一段漂亮文案，也不让用户
    看到未经计算的数字。

    Args:
        plan: TradingPlan 对象（或含 to_dict 的结果）。
        notify_cfg: notify.yaml 配置（ai 段）；None 时尝试默认路径。
    """
    text, _ = explain_plan_with_review(plan, notify_cfg)
    return text


def explain_plan_with_review(
    plan, notify_cfg: Optional[dict] = None
) -> tuple[str, Optional[AIReview]]:
    """同 ``explain_plan``，但额外返回校验结论（供看板/日志留痕）。"""
    if plan is None:
        return "（无交易计划）", None

    cfg = cfg_from_notify(notify_cfg)
    if not cfg["enabled"] or not cfg["api_key"]:
        log.info("AI 未启用或无 key，使用规则化解读")
        return ensure_disclaimer(_fallback_explain(plan)), None

    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else plan
    user = (
        "以下是量化引擎生成的一份交易计划 JSON（价格均已由量化引擎确定，"
        "请勿改动任何数值，只需解释）：\n" + json.dumps(plan_dict, ensure_ascii=False, indent=2)
    )
    text = chat_completion(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=cfg["model"],
        timeout=cfg["timeout"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
    )
    if not text:
        log.warning("AI 解读失败，使用规则化兜底")
        return ensure_disclaimer(_fallback_explain(plan)), None

    review = review_ai_text(text, plan)
    if not review.passed:
        # 校验不通过一律回退：AI 解读的定位是「解释」，一旦它开始创造事实就失去资格
        log.warning("AI 解读未通过校验（%s），回退规则化解读", review.describe())
        return ensure_disclaimer(_fallback_explain(plan)), review
    for note in review.notes:
        log.info("AI 解读校验提示：%s", note)
    return ensure_disclaimer(text), review
