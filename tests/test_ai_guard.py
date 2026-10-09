"""AI 解读守卫单元测试（纯离线）。

覆盖：数值抽取、计划白名单构造、等价变体匹配、价格语境识别、决策一致性
（含否定词处理），以及「编造价格 / 决策矛盾 → 拦截」的判定口径。
"""
from __future__ import annotations

import pytest
from quant_trading_system.stock_analysis.ai.guard import (
    DISCLAIMER,
    build_whitelist,
    check_decision_consistency,
    ensure_disclaimer,
    extract_numbers,
    is_allowed,
    plan_numbers,
    review_ai_text,
)
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState, TradingPlan


def _plan(**over) -> TradingPlan:
    base = dict(
        code="600000", name="测试股", decision=DecisionState.BUY_NOW,
        stock_score=80.0, opportunity_score=75.0, current_price=12.0,
        entry_low=11.80, entry_price=11.95, entry_high=12.10,
        stop_loss=11.35, target_1=13.20, target_2=14.50, target_3=15.80,
        risk_reward_1=2.08, risk_reward_2=4.25, position_percent=20.0,
        holding_period="5~20 个交易日", confidence=0.87,
        reasons=["测试理由"], risks=["跌破止损需离场"],
        invalidate_condition="收盘跌破 11.35 即逻辑失效",
    )
    base.update(over)
    return TradingPlan(**base)


# --------------------------------------------------------------------------- #
# 抽数
# --------------------------------------------------------------------------- #
def test_extract_numbers_basic_and_thousands_separator():
    got = [n["value"] for n in extract_numbers("目标 13.20 元，市值 1,234 亿，仓位 20%")]
    assert got == pytest.approx([13.20, 1234.0, 20.0])


def test_extract_numbers_ignores_digits_inside_identifiers():
    # 600000 这种代码不该被当成「数值事实」反复触发校验噪声
    got = [n["value"] for n in extract_numbers("代码600000，评分80")]
    assert 80.0 in got


def test_extract_numbers_carries_context_and_tail():
    items = extract_numbers("止损 11.35 元，持有 5 个交易日")
    by_val = {i["value"]: i for i in items}
    assert "元" in by_val[11.35]["tail"] or "元" in by_val[11.35]["context"]
    assert by_val[5.0]["tail"] == "个"


def test_extract_numbers_empty_text():
    assert extract_numbers("") == []
    assert extract_numbers(None) == []


# --------------------------------------------------------------------------- #
# 白名单
# --------------------------------------------------------------------------- #
def test_plan_numbers_excludes_meta_by_default():
    p = _plan()
    p.meta = {"internal_threshold": 123456.0}
    assert 123456.0 not in plan_numbers(p)
    assert 123456.0 in plan_numbers(p, include_meta=True)


def test_plan_numbers_includes_numbers_embedded_in_strings():
    nums = plan_numbers(_plan())
    assert 11.35 in nums          # invalidate_condition 里的价位
    assert 5 in nums and 20 in nums   # "5~20 个交易日"


def test_build_whitelist_has_percent_and_rounded_variants():
    wl = build_whitelist(_plan())
    assert is_allowed(12.0, wl)
    assert is_allowed(12, wl)              # 取整
    assert is_allowed(87.0, wl)            # 0.87 的百分号写法
    assert is_allowed(0.87, wl)
    assert is_allowed(2.08, wl)            # RR 原值
    assert is_allowed(2.1, wl)             # 展示层四舍五入


def test_is_allowed_rejects_unrelated_values():
    wl = build_whitelist(_plan())
    assert not is_allowed(99.99, wl)
    assert not is_allowed(1.234, wl)


def test_is_allowed_tolerance_is_tight_enough():
    wl = build_whitelist(_plan())
    assert is_allowed(11.35, wl)
    # 11.90 是 11.95 的一位小数取整变体 → 允许（展示层合理舍入）
    assert is_allowed(11.90, wl)
    # 11.60 / 9.90 无法由任何计划值取整或换算得到 → 必须拦下
    assert not is_allowed(11.60, wl)
    assert not is_allowed(9.90, wl)


# --------------------------------------------------------------------------- #
# 决策一致性
# --------------------------------------------------------------------------- #
def test_watch_rejects_buy_instruction():
    conflicts = check_decision_consistency("现在可以买入，建议建仓。",
                                           _plan(decision=DecisionState.WATCH))
    assert conflicts


def test_watch_allows_negated_buy_phrasing():
    """「不建议买入」是符合 WATCH 的表述，不能被当成买入指令。"""
    assert check_decision_consistency("当前不建议买入，建议观望。",
                                      _plan(decision=DecisionState.WATCH)) == []


def test_avoid_rejects_buy_instruction():
    assert check_decision_consistency("建议加仓，可以介入。",
                                      _plan(decision=DecisionState.AVOID))


def test_buy_now_rejects_discouraging_phrasing():
    assert check_decision_consistency("不建议买入，建议回避。",
                                      _plan(decision=DecisionState.BUY_NOW))


def test_sell_rejects_buy_instruction():
    assert check_decision_consistency("建议买入摊薄成本。",
                                      _plan(decision=DecisionState.SELL))


def test_hold_rejects_sell_without_stop_context():
    assert check_decision_consistency("建议清仓离场。",
                                      _plan(decision=DecisionState.HOLD))


def test_hold_allows_sell_mention_when_talking_about_stop():
    text = "若跌破止损则建议减仓。"
    assert check_decision_consistency(text, _plan(decision=DecisionState.HOLD)) == []


def test_consistency_is_noop_without_decision():
    assert check_decision_consistency("建议买入", {}) == []


# --------------------------------------------------------------------------- #
# 综合校验
# --------------------------------------------------------------------------- #
def test_review_passes_for_faithful_text():
    text = ("## 现在能不能买\n现价 12.00 已进入 11.80~12.10 区间，符合买入条件。\n"
            "## 入场逻辑\n标准入场 11.95，止损 11.35，目标 13.20 / 14.50 / 15.80，"
            "风险收益比 1:2.08，建议仓位 20%。")
    rev = review_ai_text(text, _plan())
    assert rev.passed is True
    assert rev.invented_prices == []
    assert rev.conflicts == []


def test_review_flags_invented_price():
    rev = review_ai_text("目标价 15.80 元，止损 9.90 元。", _plan())
    assert rev.passed is False
    assert "9.90" in rev.invented_prices
    assert "15.80" not in rev.invented_prices      # 这是计划里的目标3
    assert "编造价格" in rev.describe()


def test_review_flags_decision_conflict():
    rev = review_ai_text("建议立即买入。", _plan(decision=DecisionState.WATCH))
    assert rev.passed is False
    assert rev.conflicts
    assert "决策矛盾" in rev.describe()


def test_review_ignores_benign_counts():
    """「分三部分」「5 个交易日」这类计数不该被当成编造价格。"""
    rev = review_ai_text("分三部分说明，计划持有 5 个交易日，共 2 个目标。", _plan())
    assert rev.passed is True


def test_review_reports_unverified_numbers_without_blocking():
    """无语境的未授权数字只提示不拦截（避免守卫被噪声淹没）。"""
    rev = review_ai_text("本次统计样本 250 条。", _plan())
    assert rev.passed is True
    assert "250" in rev.unverified_numbers
    assert rev.notes


def test_review_rejects_empty_text():
    rev = review_ai_text("", _plan())
    assert rev.passed is False
    assert "解读为空" in rev.notes


def test_review_handles_none_plan():
    rev = review_ai_text("现价 12 元", None)
    assert rev.passed is False           # 无计划可比对 → 不能放行


def test_review_to_dict_roundtrip():
    rev = review_ai_text("目标价 99.99 元", _plan())
    d = rev.to_dict()
    assert set(d) == {"passed", "invented_prices", "unverified_numbers",
                      "conflicts", "notes"}
    assert d["passed"] is False


# --------------------------------------------------------------------------- #
# 免责声明
# --------------------------------------------------------------------------- #
def test_ensure_disclaimer_appends_once():
    once = ensure_disclaimer("正文")
    assert DISCLAIMER in once
    twice = ensure_disclaimer(once)
    assert twice == once                  # 不重复追加


def test_ensure_disclaimer_recognizes_equivalent_wording():
    text = "本内容不构成任何投资建议。"
    assert ensure_disclaimer(text) == text


def test_ensure_disclaimer_on_empty():
    assert ensure_disclaimer("") == DISCLAIMER


def test_disclaimer_contains_required_phrases():
    assert "不构成任何投资建议" in DISCLAIMER
    assert "风险" in DISCLAIMER
