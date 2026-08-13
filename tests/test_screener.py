"""全市场初筛器单元测试（纯离线，monkeypatch 行情源）。"""
from __future__ import annotations

import pandas as pd
from quant_trading_system.stock_analysis.screener import (
    _EXCLUDE_KEYWORDS,
    screen_candidates,
)


def _cn_spot() -> pd.DataFrame:
    """模拟 fetch_spot_snapshot 返回的 A 股快照（新 akshare 14 列基础行情）。"""
    return pd.DataFrame([
        {"code": "600519", "name": "贵州茅台", "close": 1680.5, "pct_chg": 1.2,
         "volume": 100, "amount": 8e8},
        {"code": "000001", "name": "平安银行", "close": 11.0, "pct_chg": 0.5,
         "volume": 100, "amount": 6e7},   # 6千万 高于 5kw 下限 → 通过
        {"code": "600000", "name": "浦发银行", "close": 9.0, "pct_chg": -7.0,
         "volume": 100, "amount": 3e8},   # 跌幅超 -6% → 被滤
        {"code": "000002", "name": "*ST 万科", "close": 5.0, "pct_chg": 5.0,
         "volume": 100, "amount": 2e8},   # 名称含 * → 被滤
        {"code": "601318", "name": "中国平安", "close": 50.0, "pct_chg": 2.0,
         "volume": 100, "amount": 1e9},
    ])


class TestScreenCN:
    def test_filters_and_sorts(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            _cn_spot,
        )
        out = screen_candidates("CN", top_n=10)
        codes = [c["code"] for c in out]
        # 中国平安(10亿) > 贵州茅台(8亿) > 平安银行(6千万)；浦发跌幅超限、*ST 被剔除
        assert codes == ["601318", "600519", "000001"]
        assert out[0]["name"] == "中国平安"

    def test_top_n_limits(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            _cn_spot,
        )
        out = screen_candidates("CN", top_n=2)
        assert len(out) == 2

    def test_snapshot_none_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            lambda: None,
        )
        assert screen_candidates("CN") == []


class TestScreenHK:
    def test_hk_filter(self, monkeypatch):
        hk = pd.DataFrame([
            {"代码": "00001", "中文名称": "长和", "最新价": 72.3, "涨跌幅": 1.5, "成交额": 4.3e8},
            {"代码": "00002", "中文名称": "中电控股", "最新价": 78.6, "涨跌幅": 0.9, "成交额": 3.8e8},
            {"代码": "09988", "中文名称": "阿里巴巴-W", "最新价": 82.3, "涨跌幅": 2.1, "成交额": 5e9},
            {"代码": "00005", "中文名称": "汇丰控股", "最新价": 70.0, "涨跌幅": -8.0, "成交额": 2e9},
        ])
        monkeypatch.setattr("akshare.stock_hk_spot", lambda: hk)
        out = screen_candidates("HK", top_n=10)
        codes = [c["code"] for c in out]
        # 阿里(50亿) > 长和(4.3亿) > 中电(3.8亿)；汇丰跌幅超限被滤
        assert codes == ["09988", "00001", "00002"]
        assert out[0]["name"] == "阿里巴巴-W"

    def test_hk_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr("akshare.stock_hk_spot", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert screen_candidates("HK") == []


class TestScreenUS:
    def test_nasdaq_filters_and_sorts(self, monkeypatch):
        """美股全市场：mock nasdaq API → 按市值过滤排序取 top_n。"""
        df = pd.DataFrame([
            {"symbol": "NVDA", "name": "NVIDIA Corporation Common Stock", "lastsale": "$224.09", "marketCap": "$3.4B"},
            {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$338.19", "marketCap": "$5.1B"},
            {"symbol": "PENN", "name": "Penn Entertainment Common Stock", "lastsale": "$1.50", "marketCap": "$0.2B"},
            {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "lastsale": "$590.0", "marketCap": "$0.5B"},
            {"symbol": "MSFT", "name": "Microsoft Corporation Common Stock", "lastsale": "$480.0", "marketCap": "$3.5B"},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener._fetch_nasdaq_universe",
            lambda: df,
        )
        out = screen_candidates("US", top_n=10)
        codes = [c["code"] for c in out]
        # 市值 ≥100 亿美元 + 价>2 + 剔 ETF → NVDA/AAPL/MSFT（按市值降序：AAPL>MSFT>NVDA）
        assert codes == ["AAPL", "MSFT", "NVDA"]

    def test_nasdaq_top_n_limits(self, monkeypatch):
        df = pd.DataFrame([
            {"symbol": f"S{i:04d}", "name": f"Stock {i}", "lastsale": "$50.0", "marketCap": "$1B"}
            for i in range(10)
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener._fetch_nasdaq_universe",
            lambda: df,
        )
        assert len(screen_candidates("US", top_n=3)) == 3

    def test_nasdaq_failure_falls_back(self, monkeypatch):
        """nasdaq API 失败 → 回退知名池。"""
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener._fetch_nasdaq_universe",
            lambda: None,
        )
        out = screen_candidates("US", top_n=5)
        assert len(out) == 5
        assert out[0]["code"] == "AAPL"

    def test_config_pool_prepended(self, monkeypatch):
        """兜底路径：配置池优先且去重。"""
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener._fetch_nasdaq_universe",
            lambda: None,
        )
        out = screen_candidates("US", top_n=3, config={"us_pool": ["MSFT", "TSLA"]})
        codes = [c["code"] for c in out]
        assert codes == ["MSFT", "TSLA", "AAPL"]  # 配置池优先，且去重


def test_exclude_keywords_escaped():
    """* 等正则元字符必须被转义，否则编译报错。"""
    import re

    pattern = "|".join(re.escape(k) for k in _EXCLUDE_KEYWORDS)
    assert re.compile(pattern, re.IGNORECASE)
    assert re.search(pattern, "*ST 万科") is not None
