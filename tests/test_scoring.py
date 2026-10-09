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
        # 权重和为 1。breakdown 里的 weight 是四舍五入到 3 位的**展示值**，
        # 重归一化后各维度不再是整齐的 2 位小数，累积舍入误差可达 ~2.5e-3，
        # 所以这里按展示精度断言，不按 1e-6。
        assert sum(b["weight"] for b in ss.breakdown.values()) == pytest.approx(1.0, abs=5e-3)

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
        assert set(d) == {"total", "components", "breakdown", "gated_dims"}


class TestMissingDataIsNotNeutral:
    """缺失 ≠ 中性：没有数据的维度不该按原权重计入中性 50 分。

    原实现下 `capital_flow` 权重 15%，但 `main_net` 数据源不提供 ⇒ 它恒为 50，
    对排序毫无贡献却占着 15% 的权重，**该权重完全失效**；同时 50 分还稀释了
    其他有数据的维度。实测 40 只跨行业 A 股没有一只 stock_score ≥ 60。
    """

    def test_missing_dims_are_gated_and_weights_renormalized(self):
        # 只有 pe 与 regime_score → 其余「缺数据」维度应被剔除
        ss = calc_stock_score(_kline(), extra={"pe": 25.0}, regime_score=70.0)
        assert "capital_flow" in ss.gated_dims
        assert "fundamental" in ss.gated_dims
        assert "growth" in ss.gated_dims
        assert "sector" in ss.gated_dims
        # 被剔除的维度权重归零，有数据的维度权重被放大
        assert ss.breakdown["capital_flow"]["weight"] == 0.0
        assert ss.breakdown["valuation"]["weight"] > 0.10
        assert ss.breakdown["market_env"]["weight"] > 0.05

    def test_renormalized_weights_still_sum_to_one(self):
        ss = calc_stock_score(_kline(), extra={"pe": 25.0}, regime_score=70.0)
        assert sum(b["weight"] for b in ss.breakdown.values()) == pytest.approx(1.0, abs=5e-3)

    def test_falls_back_when_too_few_dims_have_data(self):
        """数据太差（有数据维度 < 5）时回退原加权 —— 否则少数维度权重会畸高。"""
        ss = calc_stock_score(_kline())          # 没有任何 extra
        assert len(ss.gated_dims) > 4
        # 回退原加权：权重保持配置原值
        assert ss.breakdown["technical"]["weight"] == 0.20
        assert ss.breakdown["capital_flow"]["weight"] == 0.15

    def test_no_gating_when_all_data_present(self):
        ss = calc_stock_score(
            _kline(),
            extra={"pe": 25.0, "roe": 18.0, "profit_yoy": 20.0, "rev_yoy": 15.0,
                   "main_net": 1e8, "amount": 5e8},
            regime_score=70.0, sector_score=60.0,
        )
        assert ss.gated_dims == []
        assert ss.breakdown["capital_flow"]["weight"] == 0.15
        assert ss.components["fundamental"] > 50.0      # roe=18 → 高于中性

    def test_fundamental_does_not_punish_large_caps(self):
        """回归：原实现给市值 > 300 亿的股票**硬扣 10 分**，A 股蓝筹一律中招。"""
        big = calc_stock_score(_kline(), extra={"total_cap_yi": 3000.0, "turnover": 1.0})
        small = calc_stock_score(_kline(), extra={"total_cap_yi": 30.0, "turnover": 1.0})
        # 无 ROE 时基本面维度不评估（走流动性兜底），不再因「市值大」被判低分
        assert big.components["fundamental"] == small.components["fundamental"]

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

    def test_similar_pattern_default_neutral(self):
        os_ = calc_opportunity_score(_kline(), current_price=11.5, entry_low=11, entry_high=12)
        assert os_.components["similar_pattern"] == pytest.approx(50.0)

    def test_similar_pattern_uses_provided_score(self):
        os_ = calc_opportunity_score(
            _kline(), current_price=11.5, entry_low=11, entry_high=12, similar_pattern_score=80,
        )
        assert os_.components["similar_pattern"] == pytest.approx(80.0)


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
