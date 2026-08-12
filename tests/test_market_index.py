"""V2 指数行情获取与市场状态联动测试（不依赖真实网络）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.market import (
    fetch_index_kline,
    fetch_market_context,
)
from quant_trading_system.stock_analysis.market.market_regime import MarketRegimeState


def _index_df(n=160, trend=0.05, vol=0.4, seed=11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 3000 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    volume = rng.uniform(1e8, 3e8, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


class TestFetchIndexKline:
    def test_failure_returns_none(self, monkeypatch):
        """接口抛异常 → 返回 None（调用方降级中性）。"""

        def boom(*a, **kw):
            raise RuntimeError("网络不可用")

        monkeypatch.setattr("quant_trading_system.stock_analysis.market.index_data.fetch_kline_sina_api", boom)
        assert fetch_index_kline("sh000001") is None

    def test_empty_data_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.market.index_data.fetch_kline_sina_api",
            lambda *a, **kw: pd.DataFrame(),
        )
        assert fetch_index_kline("sh000001") is None

    def test_success_adds_indicators(self, monkeypatch):
        df = _index_df()

        def fake_fetch(info, days=120):
            return df[["open", "high", "low", "close", "volume"]].copy()

        monkeypatch.setattr("quant_trading_system.stock_analysis.market.index_data.fetch_kline_sina_api", fake_fetch)
        out = fetch_index_kline("sh000001", days=120)
        assert out is not None
        assert "ma20" in out.columns and "atr" in out.columns and "rsi6" in out.columns
        assert len(out) == len(df)


class TestFetchMarketContext:
    def test_context_with_real_regime(self, monkeypatch):
        df = _index_df(trend=0.05)

        def fake_fetch(info, days=120):
            return df[["open", "high", "low", "close", "volume"]].copy()

        monkeypatch.setattr("quant_trading_system.stock_analysis.market.index_data.fetch_kline_sina_api", fake_fetch)
        ctx = fetch_market_context("sh000001")
        assert ctx["regime"] is not None
        assert ctx["risk"] is not None
        assert ctx["index"] == "sh000001"
        assert ctx["index_name"] == "上证指数"
        assert ctx["close"] == pytest.approx(float(df["close"].iloc[-1]))

    def test_failure_falls_back_neutral(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.market.index_data.fetch_kline_sina_api",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        ctx = fetch_market_context("sh000001")
        assert ctx["regime"].state == MarketRegimeState.NEUTRAL
        assert ctx["regime"].factor == 0.75
        assert ctx["risk"] is not None
        assert ctx.get("close") is None
