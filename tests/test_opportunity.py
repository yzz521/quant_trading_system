"""V2 opportunity 模块单元测试（纯离线，不联网）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import (
    OpportunityEngine,
    build_trading_plan,
    calc_entry_zone,
    calc_exit_prices,
    calc_position_size,
    calc_risk_reward,
    detect_support_resistance,
)
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState


def _kline(n=160, seed=42, trend=0.03, vol=0.12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


class TestSupportResistance:
    def test_detects_levels(self):
        df = _kline()
        sr = detect_support_resistance(df)
        assert len(df) >= 30
        assert sr.supports  # 至少有一个支撑
        if sr.key_support is not None:
            assert sr.key_support < float(df["close"].iloc[-1]) * 1.02

    def test_empty_df_safe(self):
        sr = detect_support_resistance(pd.DataFrame())
        assert sr.supports == [] and sr.key_support is None

    def test_to_dict(self):
        sr = detect_support_resistance(_kline())
        d = sr.to_dict()
        assert set(d) == {"supports", "resistances", "key_support", "key_resistance", "evidence"}


class TestEntryPrice:
    def test_entry_zone_ordering(self):
        df = _kline()
        entry = calc_entry_zone(df)
        assert entry.ideal is not None and entry.standard is not None and entry.aggressive is not None
        assert entry.ideal <= entry.standard <= entry.aggressive
        assert entry.low <= entry.high
        assert entry.low == min(entry.ideal, entry.standard)

    def test_empty_safe(self):
        e = calc_entry_zone(pd.DataFrame())
        assert e.standard is None


class TestExitPrice:
    def test_stop_and_targets(self):
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        assert ex.stop_loss is not None and ex.stop_loss < entry.standard
        assert ex.target_1 is not None and ex.target_1 > entry.standard
        assert ex.target_1 < ex.target_2 < ex.target_3
        assert ex.expected_return is not None and ex.risk_reward is not None

    def test_empty_safe(self):
        ex = calc_exit_prices(pd.DataFrame())
        assert ex.stop_loss is None


class TestRiskReward:
    def test_calculation(self):
        rr = calc_risk_reward(11.95, 11.35, 13.20, 14.50)
        assert rr.risk == pytest.approx(0.60)
        assert rr.reward_1 == pytest.approx(1.25)
        assert rr.ratio_1 == pytest.approx(2.08, abs=0.01)
        assert rr.ratio_2 == pytest.approx(4.25, abs=0.01)
        assert rr.grade == "良好"

    def test_low_ratio_grade(self):
        rr = calc_risk_reward(11.95, 11.35, 12.30, 13.00)
        assert rr.grade == "不推荐"

    def test_invalid_input(self):
        rr = calc_risk_reward(0, 0, 0, 0)
        assert rr.ratio_1 is None


class TestPositionSizing:
    def test_position_bounds(self):
        ps = calc_position_size(100_000, 11.95, 11.35)
        # 单笔风险 2% = 2000 元；每股风险 0.6 → 最多 3300 股（整手）
        assert ps.max_shares == 3300
        assert ps.suggested_shares <= 3300
        assert ps.position_amount <= 100_000 * 0.20
        assert ps.position_percent <= 20.0

    def test_cap_limits(self):
        # 资金大、风险小 → 触发单票 20% 上限
        ps = calc_position_size(1_000_000, 11.95, 11.80)
        assert ps.capped is True
        assert ps.suggested_shares * 11.95 <= 1_000_000 * 0.20 + 1195

    def test_zero_equity(self):
        ps = calc_position_size(0, 11.95, 11.35)
        assert ps.suggested_shares is None


class TestTradingPlan:
    def test_build_and_decision_avoid(self):
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        rr = calc_risk_reward(entry.standard, ex.stop_loss, ex.target_1, ex.target_2)
        plan = build_trading_plan(
            code="600000", name="浦发银行", current_price=float(df["close"].iloc[-1]),
            entry=entry, exit_=ex, rr=rr, stock_score=70.0, opportunity_score=60.0,
            position_percent=10.0, confidence=0.8,
        )
        assert plan.code == "600000"
        assert plan.decision in DecisionState
        assert plan.entry_low == entry.low
        assert plan.target_1 == ex.target_1
        d = plan.to_dict()
        assert d["decision_emoji"] in ("🟢", "🟡", "🟠", "🔴", "⛔")

    def test_buy_now_when_price_in_zone(self):
        # 构造现价已落入入场区间 → BUY_NOW
        entry = type("E", (), {"low": 10.0, "high": 12.0, "ideal": 10.5, "standard": 11.0})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 14.0, "target_2": 16.0, "target_3": 18.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 2.5, "ratio_2": 4.0, "grade": "良好"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.0, entry=entry, exit_=ex, rr=rr,
            stock_score=80, opportunity_score=80,
        )
        assert plan.decision == DecisionState.BUY_NOW

    def test_buy_on_pullback_when_elevated(self):
        entry = type("E", (), {"low": 10.0, "high": 10.5, "ideal": 10.2, "standard": 10.3})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 13.0, "target_2": 15.0, "target_3": 17.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 2.8, "ratio_2": 5.0, "grade": "良好"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.3, entry=entry, exit_=ex, rr=rr,
            stock_score=80, opportunity_score=80,
        )
        assert plan.decision == DecisionState.BUY_ON_PULLBACK


class TestOpportunityEngine:
    def test_full_analysis(self):
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze("600000", "测试", df, extra={"total_cap_yi": 120, "pe": 25, "turnover": 2.0})
        assert res.plan is not None
        assert res.sr is not None and res.entry is not None and res.exit_ is not None
        assert res.rr is not None and res.stock_score is not None and res.opportunity_score is not None
        assert res.plan.current_price is not None
        assert res.plan.invalidate_condition  # 应有失效条件
        d = res.to_dict()
        assert d["plan"]["code"] == "600000"

    def test_short_df_returns_empty(self):
        df = _kline(n=20)
        res = OpportunityEngine().analyze("1", "t", df)
        assert res.plan is None
