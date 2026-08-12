"""V2 market 模块单元测试（纯离线）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.market import (
    REGIME_FACTOR,
    MarketRegimeState,
    calc_market_breadth,
    calc_market_risk,
    detect_market_regime,
)


def _index_df(n=120, trend=0.05, vol=0.4, seed=11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 3000 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    volume = rng.uniform(1e8, 3e8, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


class TestMarketRegime:
    def test_bull_detection(self):
        df = _index_df(trend=0.05)
        r = detect_market_regime(df)
        assert r.state == MarketRegimeState.BULL
        assert r.score >= 70
        assert r.factor == REGIME_FACTOR[MarketRegimeState.BULL]

    def test_bear_detection(self):
        df = _index_df(trend=-0.8, vol=0.5)
        r = detect_market_regime(df)
        assert r.state in (MarketRegimeState.BEAR, MarketRegimeState.HIGH_RISK)
        assert r.factor <= REGIME_FACTOR[MarketRegimeState.NEUTRAL]

    def test_empty_defaults_neutral(self):
        r = detect_market_regime(pd.DataFrame())
        assert r.state == MarketRegimeState.NEUTRAL
        assert r.factor == 0.75

    def test_to_dict(self):
        r = detect_market_regime(_index_df())
        d = r.to_dict()
        assert set(d) == {"state", "score", "factor", "evidence"}


class TestMarketBreadth:
    def test_positive_market(self):
        spot = pd.DataFrame({"pct_chg": np.random.default_rng(1).normal(1.0, 1.5, 3000)})
        b = calc_market_breadth(spot)
        assert b.advance > b.decline
        assert b.ratio > 0.5
        assert b.score > 50

    def test_negative_market(self):
        spot = pd.DataFrame({"pct_chg": np.random.default_rng(2).normal(-1.0, 1.5, 3000)})
        b = calc_market_breadth(spot)
        assert b.decline > b.advance
        assert b.score < 50

    def test_empty(self):
        b = calc_market_breadth(pd.DataFrame())
        assert b.score == 50.0


class TestMarketRisk:
    def test_low_risk(self):
        df = _index_df(trend=0.02, vol=0.2)
        r = calc_market_risk(df)
        assert r.score >= 60
        assert r.level in ("LOW", "MEDIUM")

    def test_high_volatility_lowers_score(self):
        calm = _index_df(seed=3, trend=0.0, vol=0.1)
        wild = _index_df(seed=4, trend=0.0, vol=2.0)
        assert calc_market_risk(wild).score <= calc_market_risk(calm).score

    def test_empty(self):
        r = calc_market_risk(pd.DataFrame())
        assert r.score == 80.0
