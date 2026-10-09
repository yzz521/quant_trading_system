"""全市场初筛器单元测试（纯离线，monkeypatch 行情源）。"""
from __future__ import annotations

import pandas as pd
import pytest
from quant_trading_system.stock_analysis.screener import (
    _EXCLUDE_KEYWORDS,
    screen_candidates,
)


def _cn_spot(min_amount=None) -> pd.DataFrame:
    """模拟 fetch_spot_snapshot 返回的 A 股快照（新 akshare 14 列基础行情）。

    注意签名要接受 ``min_amount``：``_screen_cn`` 会把它透传下去，
    换成候选源后只按成交额降序取前几页（避免全市场 60 次请求）。
    """
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
            lambda min_amount=None: None,
        )
        assert screen_candidates("CN") == []

    def test_cn_passes_min_amount_to_snapshot(self, monkeypatch):
        """初筛要把成交额阈值透传下去（候选源据此只取前几页）。"""
        seen = {}

        def _fake(min_amount=None):
            seen["min_amount"] = min_amount
            return _cn_spot()

        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot", _fake
        )
        screen_candidates("CN", top_n=3, min_amount=2e8)
        assert seen["min_amount"] == 2e8


class TestScreenHK:
    def test_hk_filter(self, monkeypatch):
        # 东财候选源不可用 → 回退 akshare 旧源（本次只验证后者）
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.data_fetcher.fetch_spot_candidates_em",
            lambda *a, **k: None,
        )
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

    def test_hk_uses_em_first(self, monkeypatch):
        """东财可用时不再走（已失效的）akshare 新浪源。"""
        df = pd.DataFrame([
            {"code": "00001", "name": "长和", "close": 72.3, "pct_chg": 1.5, "amount": 4.3e8,
             "volume": 1, "turnover": 1, "pe": 1, "pb": 1,
             "total_cap_yi": 2000, "float_cap_yi": 2000},
            {"code": "00700", "name": "腾讯控股", "close": 620.0, "pct_chg": 2.5, "amount": 5e9,
             "volume": 1, "turnover": 1, "pe": 1, "pb": 1,
             "total_cap_yi": 50000, "float_cap_yi": 50000},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.data_fetcher.fetch_spot_candidates_em",
            lambda *a, **k: df,
        )
        monkeypatch.setattr("akshare.stock_hk_spot",
                            lambda: (_ for _ in ()).throw(AssertionError("不该回退")))
        out = screen_candidates("HK", top_n=5)
        assert [c["code"] for c in out] == ["00700", "00001"]

    def test_hk_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.data_fetcher.fetch_spot_candidates_em",
            lambda *a, **k: None,
        )
        monkeypatch.setattr("akshare.stock_hk_spot", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert screen_candidates("HK") == []


class TestScreenUS:
    def test_nasdaq_filters_and_sorts(self, monkeypatch):
        """美股全市场：mock nasdaq API → 按市值过滤排序取 top_n。

        夹具用**真实量级的字符串**（万亿用 T 后缀）：原夹具写的是 ``$3.4B``，
        既不符合真实市值，也正好绕过了「T 后缀解析失败」这个缺陷。
        """
        df = pd.DataFrame([
            {"symbol": "NVDA", "name": "NVIDIA Corporation Common Stock", "lastsale": "$224.09", "marketCap": "$2.8T"},
            {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$338.19", "marketCap": "$3.5T"},
            {"symbol": "PENN", "name": "Penn Entertainment Common Stock", "lastsale": "$1.50", "marketCap": "$0.2B"},
            {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "lastsale": "$590.0", "marketCap": "$0.5T"},
            {"symbol": "MSFT", "name": "Microsoft Corporation Common Stock", "lastsale": "$480.0", "marketCap": "$3.1T"},
            # 价 >2 但市值仅 50 亿美元 → 必须被「≥100 亿」门槛挡掉
            {"symbol": "SMALL", "name": "Small Cap Corp Common Stock", "lastsale": "$50.0", "marketCap": "$5B"},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener._fetch_nasdaq_universe",
            lambda: df,
        )
        out = screen_candidates("US", top_n=10)
        codes = [c["code"] for c in out]
        # 市值 ≥100 亿美元 + 价>2 + 剔 ETF → AAPL/MSFT/NVDA（按市值降序）
        assert codes == ["AAPL", "MSFT", "NVDA"]
        assert "SMALL" not in codes
        # 市值单位固定为亿美元：AAPL 3.5T USD = 35000 亿
        aapl = next(c for c in out if c["code"] == "AAPL")
        assert aapl["total_cap_yi"] == pytest.approx(35_000.0)

    def test_nasdaq_top_n_limits(self, monkeypatch):
        df = pd.DataFrame([
            {"symbol": f"S{i:04d}", "name": f"Stock {i}", "lastsale": "$50.0", "marketCap": "$500B"}
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


class TestParseUsMarketCap:
    """回归缺陷：``$3.4T`` 解析成 NaN（大盘股被丢）、``$3.4B`` 放大 10 倍。"""

    def test_suffixes(self):
        from quant_trading_system.stock_analysis.screener import _parse_us_market_cap as f

        assert f("$3.4T") == pytest.approx(34_000.0)     # 万亿 → 亿美元
        assert f("$850.0B") == pytest.approx(8_500.0)    # 十亿 → 亿美元
        assert f("$120M") == pytest.approx(1.2)          # 百万 → 亿美元
        assert f("$1,234.5B") == pytest.approx(12_345.0)  # 带千分位

    def test_no_suffix_treated_as_usd(self):
        from quant_trading_system.stock_analysis.screener import _parse_us_market_cap as f

        assert f("5000000000") == pytest.approx(50.0)    # 50 亿美元

    def test_missing_and_garbage(self):
        from quant_trading_system.stock_analysis.screener import _parse_us_market_cap as f

        for bad in (None, "", "N/A", "-", "abc", "$0", "$-5", float("nan")):
            assert f(bad) is None

    def test_t_suffix_no_longer_vanishes(self):
        """核心断言：万亿级市值必须解析成功，而不是 NaN。"""
        from quant_trading_system.stock_analysis.screener import _parse_us_market_cap as f

        v = f("$3.4T")
        assert v is not None and v > 0


def test_exclude_keywords_escaped():
    """* 等正则元字符必须被转义，否则编译报错。"""
    import re

    pattern = "|".join(re.escape(k) for k in _EXCLUDE_KEYWORDS)
    assert re.compile(pattern, re.IGNORECASE)
    assert re.search(pattern, "*ST 万科") is not None


class TestNameExclusion:
    """名称剔除规则：子串只用于 ST/退/*，N/C 只在前缀生效。"""

    def test_excludes_risk_warning_names(self):
        from quant_trading_system.stock_analysis.screener import _name_excluded

        assert _name_excluded("*ST 万科") is True
        assert _name_excluded("ST 康美") is True
        assert _name_excluded("退市海润") is True

    def test_does_not_exclude_latin_letters_in_middle(self):
        """回归：原实现用子串匹配 C/N，会把 TCL科技 误杀。"""
        from quant_trading_system.stock_analysis.screener import _name_excluded

        assert _name_excluded("TCL科技") is False
        assert _name_excluded("贵州茅台") is False
        assert _name_excluded("中远海控") is False
        # 纯拉丁名称（港美股）不受 N/C 前缀规则影响
        assert _name_excluded("NIO Inc") is False
        assert _name_excluded("C3.ai") is False

    def test_excludes_new_listing_prefixes(self):
        from quant_trading_system.stock_analysis.screener import _name_excluded

        assert _name_excluded("N华虹") is True
        assert _name_excluded("C华虹") is True


class TestStratifiedSelection:
    """候选池必须做行业分散，不能整池集中在同一个行业。"""

    def _spot(self) -> pd.DataFrame:
        rows = []
        # 同一行业 6 只（成交额很高），另外 3 个行业各 2 只（成交额较低）
        for i in range(6):
            rows.append({"code": f"60000{i}", "name": f"芯片{i}", "close": 10.0,
                         "pct_chg": 1.0, "volume": 100, "amount": 9e8 - i * 1e6,
                         "float_cap_yi": 100.0})
        for k, ind in enumerate(["银行", "医药", "白酒"]):
            for i in range(2):
                rows.append({"code": f"0000{k}{i}", "name": f"{ind}{i}", "close": 10.0,
                             "pct_chg": 1.0, "volume": 100, "amount": 3e8 - i * 1e6,
                             "float_cap_yi": 80.0})
        return pd.DataFrame(rows)

    def test_single_industry_capped(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            lambda min_amount=None: self._spot(),
        )
        imap = {f"60000{i}": "半导体" for i in range(6)}
        imap.update({"000000": "银行", "000001": "银行", "000010": "医药", "000011": "医药",
                     "000020": "白酒", "000021": "白酒"})
        out = screen_candidates("CN", top_n=6, industry_map=imap, per_industry_ratio=0.25)
        inds = [c.get("industry") for c in out]
        assert len(out) == 6
        assert inds.count("半导体") <= 2          # cap = max(2, 6*0.25) = 2
        assert len(set(inds)) == 4                # 4 个行业都进池 → 真的分散了

    def test_passthrough_fields_present(self, monkeypatch):
        """候选 dict 必须带上质量闸门需要的字段，避免下游再取一次快照。"""
        df = pd.DataFrame([
            {"code": "600001", "name": "测试股", "close": 10.0, "pct_chg": 1.0,
             "volume": 100, "amount": 9e8, "turnover": 2.5, "pe": 18.0,
             "total_cap_yi": 300.0, "float_cap_yi": 200.0, "pb": 2.1},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            lambda min_amount=None: df,
        )
        out = screen_candidates("CN", top_n=3)
        assert out
        c = out[0]
        for key in ("code", "name", "amount", "pe", "turnover",
                    "total_cap_yi", "float_cap_yi", "pct_chg"):
            assert key in c, f"缺少透传字段 {key}"
        assert c["pe"] == 18.0 and c["turnover"] == 2.5

    def test_min_float_cap_filters_micro_caps(self, monkeypatch):
        df = pd.DataFrame([
            {"code": "600001", "name": "大盘股", "close": 10.0, "pct_chg": 1.0,
             "volume": 100, "amount": 9e8, "float_cap_yi": 500.0},
            {"code": "600002", "name": "微盘股", "close": 10.0, "pct_chg": 1.0,
             "volume": 100, "amount": 8e8, "float_cap_yi": 5.0},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            lambda min_amount=None: df,
        )
        out = screen_candidates("CN", top_n=5, min_float_cap_yi=20.0)
        assert [c["code"] for c in out] == ["600001"]

    def test_zero_amount_excluded(self, monkeypatch):
        """停牌/零成交行必须剔除。"""
        df = pd.DataFrame([
            {"code": "600001", "name": "正常", "close": 10.0, "pct_chg": 1.0,
             "volume": 100, "amount": 9e8},
            {"code": "600002", "name": "停牌", "close": 10.0, "pct_chg": 0.0,
             "volume": 0, "amount": 0.0},
        ])
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.screener.fetch_spot_snapshot",
            lambda min_amount=None: df,
        )
        out = screen_candidates("CN", top_n=5)
        assert [c["code"] for c in out] == ["600001"]
