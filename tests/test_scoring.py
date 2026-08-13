"""V2 scoring 模块单元测试（纯离线）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.scoring import (
    calc_opportunity_score,
    calc_stock_score,
    score_price_position,
    score_rr,
    score_support_strength,
    score_trend,
    score_volatility,
    score_volume,
)


def _kline(n=160, seed=7, trend=0.03, vol=0.12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


class TestStockScore:
    def test_total_within_range(self):
        df = _kline()
        ss = calc_stock_score(df, extra={"total_cap_yi": 120, "pe": 25, "turnover": 2.0}, regime_score=70)
        assert 0 <= ss.total <= 100
        assert ss.components["technical"] >= 0
        # 权重和为 1
        assert sum(b["weight"] for b in ss.breakdown.values()) == pytest.approx(1.0)

    def test_weights_follow_plan(self):
        ss = calc_stock_score(None)
        w = {k: b["weight"] for k, b in ss.breakdown.items()}
        # 9 因子（Factor Engine）
        assert w["technical"] == 0.20
        assert w["risk"] == 0.20
        assert w["fundamental"] == 0.12
        assert w["growth"] == 0.08
        assert w["momentum"] == 0.05
        assert w["capital_flow"] == 0.15
        assert w["valuation"] == 0.10
        assert w["market_env"] == 0.05
        assert w["sector"] == 0.05

    def test_growth_neutral_without_data(self):
        """无成长数据 → growth 因子 50 中性（不拖累总分）。"""
        ss = calc_stock_score(_kline())
        assert ss.components["growth"] == pytest.approx(50.0)

    def test_growth_responds_to_extra(self):
        """净利同比高 → growth 分上升；净利同比负 → 下降。"""
        ss_good = calc_stock_score(_kline(), extra={"rev_yoy": 30.0, "profit_yoy": 40.0})
        ss_bad = calc_stock_score(_kline(), extra={"rev_yoy": -20.0, "profit_yoy": -30.0})
        assert ss_good.components["growth"] > 50.0
        assert ss_bad.components["growth"] < 50.0

    def test_sector_factor_affects_score(self):
        """板块强度传入 → sector 因子反映；缺失为中性 50。"""
        ss_neutral = calc_stock_score(_kline())
        ss_hot = calc_stock_score(_kline(), sector_score=95.0)
        assert ss_neutral.components["sector"] == pytest.approx(50.0)
        assert ss_hot.components["sector"] == pytest.approx(95.0)

    def test_momentum_range(self):
        """momentum 因子始终在 0-100。"""
        ss = calc_stock_score(_kline())
        assert 0 <= ss.components["momentum"] <= 100

    def test_to_dict(self):
        ss = calc_stock_score(_kline())
        d = ss.to_dict()
        assert set(d) == {"total", "components", "breakdown"}

    def test_news_risk_penalty(self):
        df = _kline()
        ss_clean = calc_stock_score(df)
        ss_risky = calc_stock_score(df, news_risks=[{"title": "立案调查"}])
        assert ss_risky.components["risk"] <= ss_clean.components["risk"]


class TestOpportunityScore:
    def test_total_within_range(self):
        df = _kline()
        os_ = calc_opportunity_score(
            df, current_price=float(df["close"].iloc[-1]),
            entry_low=11.0, entry_high=12.0, key_support=10.5, risk_reward_1=2.5,
        )
        assert 0 <= os_.total <= 100

    def test_distance_score(self):
        # 现价在区间内 → distance 高分
        os_ = calc_opportunity_score(
            _kline(), current_price=11.5, entry_low=11.0, entry_high=12.0, risk_reward_1=2.5,
        )
        assert os_.components["distance_to_entry"] >= 90
        # 现价远离区间上方 → distance 低分
        os_high = calc_opportunity_score(
            _kline(), current_price=15.0, entry_low=11.0, entry_high=12.0, risk_reward_1=2.5,
        )
        assert os_high.components["distance_to_entry"] < 50

    def test_weights_sum_one(self):
        os_ = calc_opportunity_score(_kline(), current_price=11.5, entry_low=11, entry_high=12)
        assert sum(b["weight"] for b in os_.breakdown.values()) == pytest.approx(1.0)


class TestComponents:
    def test_rr_scoring(self):
        assert score_rr(3.5) == 95.0
        assert score_rr(2.2) == 75.0
        assert score_rr(1.6) == 55.0
        assert score_rr(1.0) == 25.0
        assert score_rr(None) == 40.0

    def test_trend_bull_high(self):
        # 强多头排列（MA5>MA20>MA60，现价在上方）→ 趋势高分
        df = _kline(trend=0.08, seed=3)
        s = score_trend(df)
        assert s >= 60

    def test_components_within_range(self):
        df = _kline()
        for fn in (score_trend, score_volume, score_volatility, score_price_position):
            assert 0 <= fn(df) <= 100

    def test_support_strength(self):
        df = _kline()
        s = score_support_strength(df, key_support=float(df["close"].iloc[-1]) * 0.97)
        assert 0 <= s <= 100
