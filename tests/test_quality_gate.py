"""质量闸门与组合分测试（P0）。

验证「推荐不是坏票」这条硬约束：
  * 关键数据缺失只降级、不加分（不再用中性 50 掩盖）
  * 亏损股 / 重大风险 / 流动性不足 → 硬否决（AVOID）
  * 分数不够 / 数据不全 → 降级为 WATCH，且给出可展示的原因
  * 组合分把「质量」真正纳入排序
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import (
    OpportunityEngine,
    QualityGateConfig,
    build_trading_plan,
    compute_coverage,
    evaluate,
)
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState
from quant_trading_system.stock_analysis.scoring.composite import (
    COMPOSITE_WEIGHTS,
    composite_score,
    rr_component,
    score_plan,
)


def _full_extra(**over) -> dict:
    base = {
        "pe": 22.0, "total_cap_yi": 300.0, "turnover": 2.0,
        "roe": 15.0, "profit_yoy": 20.0, "rev_yoy": 12.0, "main_net": 1e7,
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


# --------------------------------------------------------------------------- #
# 数据完整度
# --------------------------------------------------------------------------- #
class TestDataCoverage:
    def test_full_coverage(self):
        cov = compute_coverage(_full_extra())
        assert cov.ratio == pytest.approx(1.0)
        assert cov.has_core and cov.has_quality
        assert cov.missing == []

    def test_snapshot_only_coverage(self):
        """只有快照字段（PE/市值/换手）→ 覆盖部分，且无盈利数据。"""
        cov = compute_coverage({"pe": 20.0, "total_cap_yi": 100.0, "turnover": 1.5})
        assert 0.0 < cov.ratio < 1.0
        assert cov.has_core is True
        assert cov.has_quality is False
        assert "roe" in cov.missing

    def test_nan_treated_as_missing(self):
        cov = compute_coverage({"pe": float("nan"), "total_cap_yi": 100.0})
        assert cov.present["pe"] is False

    def test_empty_extra(self):
        cov = compute_coverage(None)
        assert cov.ratio == 0.0 and cov.n_present == 0


# --------------------------------------------------------------------------- #
# 闸门判定
# --------------------------------------------------------------------------- #
class TestGateEvaluate:
    def test_passes_when_everything_ok(self):
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        assert r.passed is True
        assert r.tier == "BUY"
        assert r.reasons == []

    def test_low_stock_score_downgrades_to_watch(self):
        r = evaluate(
            stock_score=45.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "WATCH"
        assert r.passed is False
        assert any("个股质量分" in x for x in r.reasons)

    def test_low_rr_downgrades(self):
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=1.6,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "WATCH"
        assert any("风险收益比" in x for x in r.reasons)

    def test_missing_rr_downgrades_instead_of_passing(self):
        """回归：RR 缺失（参数不全 / 止损距离低于可交易下限）时必须降级。

        原实现写的是 `if risk_reward_1 is not None and cfg.min_rr > 0:` ——
        RR 为 None 时直接**跳过**检查 = 放行，等于「测不出来就当通过」。
        而 None 恰恰来自「止损贴着入场价」那类计划（RR 被数学放大成几十上百倍），
        本该是最该拦下的（见 risk_reward.MIN_RISK_PCT）。
        """
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=None,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "WATCH"
        assert r.checks["risk_reward"]["ok"] is False
        assert any("无法评估赔率" in x for x in r.reasons)

    def test_severe_news_hard_rejects(self):
        r = evaluate(
            stock_score=90.0, opportunity_score=90.0, risk_reward_1=4.0,
            extra=_full_extra(), risk_component=90.0, amount=5e8, severe_news=True,
        )
        assert r.tier == "REJECT"
        assert r.checks["severe_news"]["hard"] is True

    def test_negative_pe_hard_rejects(self):
        r = evaluate(
            stock_score=80.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(pe=-12.0), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "REJECT"
        assert any("亏损" in x for x in r.reasons)

    def test_excessive_pe_hard_rejects(self):
        r = evaluate(
            stock_score=80.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(pe=400.0), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "REJECT"
        assert any("估值过高" in x for x in r.reasons)

    def test_low_liquidity_hard_rejects(self):
        r = evaluate(
            stock_score=80.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=80.0, amount=1e6,
        )
        assert r.tier == "REJECT"
        assert any("成交额" in x for x in r.reasons)

    def test_micro_cap_hard_rejects(self):
        r = evaluate(
            stock_score=80.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(total_cap_yi=8.0), risk_component=80.0, amount=5e8,
        )
        assert r.tier == "REJECT"

    def test_missing_quality_data_downgrades(self):
        """核心回归：没有盈利/成长数据时必须降级，而不是按中性 50 分放行。"""
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra={"pe": 20.0, "total_cap_yi": 200.0, "turnover": 2.0},
            risk_component=80.0, amount=5e8,
        )
        assert r.tier == "WATCH"
        assert any("缺少盈利/成长数据" in x for x in r.reasons)

    def test_low_risk_component_downgrades(self):
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=30.0, amount=5e8,
        )
        assert r.tier == "WATCH"
        assert any("风险维度分" in x for x in r.reasons)

    def test_disabled_always_passes(self):
        r = evaluate(
            stock_score=1.0, opportunity_score=1.0, risk_reward_1=0.1,
            extra={}, severe_news=True, amount=0.0,
            config=QualityGateConfig.disabled(),
        )
        assert r.passed is True and r.tier == "BUY"

    def test_for_backtest_relaxes_data_but_keeps_scores(self):
        """回测口径：不因缺财务数据被拒，但分数门槛仍然生效。"""
        cfg = QualityGateConfig.for_backtest()
        ok = evaluate(
            stock_score=70.0, opportunity_score=70.0, risk_reward_1=2.5,
            extra={}, config=cfg,
        )
        assert ok.tier == "BUY"
        bad = evaluate(
            stock_score=30.0, opportunity_score=70.0, risk_reward_1=2.5,
            extra={}, config=cfg,
        )
        assert bad.tier == "WATCH"

    def test_to_dict_shape(self):
        r = evaluate(
            stock_score=75.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        d = r.to_dict()
        assert set(d) == {"passed", "tier", "failed", "reasons", "coverage", "checks"}
        assert d["coverage"]["ratio"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# build_trading_plan 的降级逻辑
# --------------------------------------------------------------------------- #
def _entry():
    return type("E", (), {"low": 10.0, "high": 12.0, "ideal": 10.5, "standard": 11.0})()


def _exit():
    return type("X", (), {"stop_loss": 9.5, "target_1": 14.0, "target_2": 16.0,
                          "target_3": 18.0, "stop_source": "t"})()


def _rr(ratio=2.5):
    return type("R", (), {"ratio_1": ratio, "ratio_2": 4.0, "grade": "良好"})()


class TestPlanGateIntegration:
    def _plan(self, gate, decision_price=11.0):
        return build_trading_plan(
            code="1", name="t", current_price=decision_price,
            entry=_entry(), exit_=_exit(), rr=_rr(),
            stock_score=80, opportunity_score=80, gate=gate,
        )

    def test_gate_watch_downgrades_buy_now(self):
        gate = evaluate(
            stock_score=45.0, opportunity_score=80.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=80.0, amount=5e8,
        )
        plan = self._plan(gate)
        assert plan.decision == DecisionState.WATCH
        assert "gate_downgrade" in plan.meta
        assert plan.meta["quality_gate"]["tier"] == "WATCH"

    def test_gate_reject_forces_avoid(self):
        gate = evaluate(
            stock_score=90.0, opportunity_score=90.0, risk_reward_1=4.0,
            extra=_full_extra(), risk_component=90.0, amount=5e8, severe_news=True,
        )
        plan = self._plan(gate)
        assert plan.decision == DecisionState.AVOID

    def test_gate_buy_keeps_buy_now(self):
        gate = evaluate(
            stock_score=80.0, opportunity_score=85.0, risk_reward_1=3.0,
            extra=_full_extra(), risk_component=85.0, amount=5e8,
        )
        plan = self._plan(gate)
        assert plan.decision == DecisionState.BUY_NOW

    def test_no_gate_keeps_legacy_behaviour(self):
        plan = self._plan(None)
        assert plan.decision == DecisionState.BUY_NOW

    def test_timestamps_recorded_in_meta(self):
        plan = build_trading_plan(
            code="1", name="t", current_price=11.0, entry=_entry(), exit_=_exit(),
            rr=_rr(), stock_score=80, opportunity_score=80,
            price_as_of="2026-09-24", bar_as_of="2026-09-24",
            fundamental_as_of="2026Q2", data_coverage=0.83,
        )
        assert plan.meta["price_as_of"] == "2026-09-24"
        assert plan.meta["fundamental_as_of"] == "2026Q2"
        assert plan.meta["data_coverage"] == pytest.approx(0.83)


# --------------------------------------------------------------------------- #
# 引擎集成
# --------------------------------------------------------------------------- #
def _kline(n=200, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0.03, 0.12, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": volume, "amount": volume * close,
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
    })
    return add_all_indicators(df)


class TestEngineGateIntegration:
    def test_plan_carries_gate_and_timestamps(self):
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze("600000", "测试", df, extra=_full_extra())
        assert res.plan is not None
        gate = res.plan.meta["quality_gate"]
        assert gate["tier"] in ("BUY", "WATCH", "REJECT")
        assert "coverage" in gate
        assert res.plan.meta.get("bar_as_of")        # 必须记录数据时点

    def test_missing_extra_downgrades_from_buy(self):
        """核心回归：不给任何财务数据时，不允许直接输出买入状态。"""
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze("600000", "测试", df, extra={})
        assert res.plan is not None
        assert res.plan.decision != DecisionState.BUY_NOW
        assert res.plan.meta["quality_gate"]["tier"] in ("WATCH", "REJECT")

    def test_gate_reasons_appear_in_risks(self):
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze("600000", "测试", df, extra={})
        assert res.plan is not None
        if res.plan.decision != DecisionState.BUY_NOW:
            assert any("质量闸门" in r for r in res.plan.risks)

    def test_gate_disabled_restores_legacy(self):
        df = _kline()
        eng = OpportunityEngine(
            account_equity=100_000, regime_score=70,
            quality_gate=QualityGateConfig.disabled(),
        )
        res = eng.analyze("600000", "测试", df, extra={})
        assert res.plan is not None
        assert res.plan.meta["quality_gate"]["passed"] is True


# --------------------------------------------------------------------------- #
# 组合分
# --------------------------------------------------------------------------- #
class TestCompositeScore:
    def test_weights_sum_to_one(self):
        assert sum(COMPOSITE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_rr_component_capped(self):
        assert rr_component(4.0) == pytest.approx(100.0)
        assert rr_component(10.0) == pytest.approx(100.0)
        assert rr_component(2.0) == pytest.approx(50.0)
        assert rr_component(None) == 0.0
        assert rr_component(-1.0) == 0.0

    def test_quality_drives_ranking(self):
        """高机会分但质量差的票，不应排在高机会分且质量好的票前面。"""
        good = composite_score(stock_score=80, opportunity_score=75, risk_reward_1=2.5)
        bad = composite_score(stock_score=35, opportunity_score=85, risk_reward_1=2.5)
        assert good > bad

    def test_missing_dimensions_not_rewarded(self):
        """缺失按 0 计，而不是中性 50 —— 缺失不该被奖励。"""
        s = composite_score(stock_score=None, opportunity_score=None, risk_reward_1=None)
        assert s == 0.0

    def test_score_plan_handles_none(self):
        assert score_plan(None) == 0.0
        assert score_plan({"stock_score": 60, "opportunity_score": 60,
                           "risk_reward_1": 2.0}) > 0


# --------------------------------------------------------------------------- #
# 市值上限与检查项文案（回归）
# --------------------------------------------------------------------------- #
class TestMarketCapCeiling:
    """回归缺陷：``max_total_cap_yi`` 配置存在但**从未被检查** —— 一个 3 万亿
    市值的标的和 300 亿的标的在闸门眼里完全一样。"""

    def test_mega_cap_above_ceiling_is_rejected(self):
        cfg = QualityGateConfig()
        r = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(total_cap_yi=30_000.0), risk_component=80.0,
                     amount=1e9, config=cfg)
        assert r.tier == "REJECT"
        assert any("超过上限" in x for x in r.reasons)
        assert r.checks["market_cap"]["ok"] is False

    def test_cap_just_below_ceiling_passes(self):
        cfg = QualityGateConfig()
        r = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(total_cap_yi=cfg.max_total_cap_yi), risk_component=80.0,
                     amount=1e9, config=cfg)
        assert r.tier == "BUY"
        assert r.checks["market_cap"]["ok"] is True

    def test_cap_exactly_at_ceiling_is_allowed(self):
        cfg = QualityGateConfig(max_total_cap_yi=20_000.0)
        r = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(total_cap_yi=20_000.0), risk_component=80.0,
                     amount=1e9, config=cfg)
        assert r.tier == "BUY"

    def test_backtest_config_has_no_ceiling(self):
        """回测放宽版必须放行超大盘（``inf`` 上限不能把一切挡在门外）。"""
        r = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(total_cap_yi=99_999_999.0), risk_component=80.0,
                     amount=1e9, config=QualityGateConfig.for_backtest())
        assert r.tier == "BUY"


class TestCheckMessagesReadCorrectly:
    """回归缺陷：``_record`` 无论通过与否都写同一句「…低于下限…」，审计时会把
    「通过」读成「不通过」。"""

    def test_passing_checks_have_positive_messages(self):
        r = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(), risk_component=80.0, amount=1e9,
                     config=QualityGateConfig())
        assert r.tier == "BUY"
        assert r.reasons == []
        for name, c in r.checks.items():
            if c["ok"]:
                assert "低于下限" not in c["msg"], f"{name} 通过却写着「低于下限」"
                assert "< 门槛" not in c["msg"], f"{name} 通过却写着「< 门槛」"

    def test_failing_checks_still_use_failure_wording(self):
        r = evaluate(stock_score=30, opportunity_score=80, risk_reward_1=3.0,
                     extra=_full_extra(), risk_component=80.0, amount=1e9,
                     config=QualityGateConfig())
        assert r.tier == "WATCH"
        assert any("< 门槛" in x for x in r.reasons)
        assert r.checks["stock_score"]["ok"] is False

    def test_liquidity_message_direction(self):
        """流动性不足时写「低于下限」，达标时不能还这么写。"""
        low = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                       extra=_full_extra(), risk_component=80.0, amount=1e5,
                       config=QualityGateConfig())
        assert any("低于下限" in x for x in low.reasons)
        ok = evaluate(stock_score=80, opportunity_score=80, risk_reward_1=3.0,
                      extra=_full_extra(), risk_component=80.0, amount=1e9,
                      config=QualityGateConfig())
        assert "低于下限" not in ok.checks["liquidity"]["msg"]
