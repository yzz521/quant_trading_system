"""V2 批量机会扫描器单元测试（纯离线）。

用 FakeEngine 注入确定性 TradingPlan，直接验证过滤/排序/失败降级逻辑，
不依赖合成数据形态。
"""
from __future__ import annotations

import pandas as pd
from quant_trading_system.stock_analysis.data_fetcher import detect_market
from quant_trading_system.stock_analysis.opportunity import OpportunityBatchScanner
from quant_trading_system.stock_analysis.opportunity.batch_scanner import _default_loader
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState, TradingPlan


class TestDefaultLoader:
    """默认 loader 必须用线程安全接口（防并发崩溃回归）。

    批量扫描是多线程的，akshare 的 stock_zh_a_daily / stock_hk_daily 内置
    非线程安全 mini_racer JS 引擎，多线程并发必崩（libmini_racer
    address_pool_manager Check failed）。默认 loader 按市场走线程安全源：
    CN=新浪 / HK=腾讯 / US=yfinance。
    """

    def test_loader_uses_threadsafe_sources(self):
        import inspect

        from quant_trading_system.stock_analysis import data_fetcher

        src = inspect.getsource(_default_loader)
        # CN 走新浪线程安全接口
        assert "fetch_kline_sina_api" in src
        # HK 走腾讯线程安全接口（防 akshare mini_racer 崩溃）
        assert "fetch_kline_hk_tencent" in src
        # US 走 fetch_kline（内部 yfinance，线程安全）
        assert "info.market == \"HK\"" in src and "fetch_kline(info" in src
        # 同时确认新浪/腾讯接口本身是纯 urllib 实现（函数体内不 import akshare）
        import re

        for fn in (data_fetcher.fetch_kline_sina_api, data_fetcher.fetch_kline_hk_tencent):
            fsrc = inspect.getsource(fn)
            assert "urllib" in fsrc
            body = fsrc.split('"""')[-1]
            assert not re.search(r"^\s*(import|from)\s+akshare", body, re.M), \
                f"{fn.__name__} 不应依赖 akshare"

    def test_loader_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.opportunity.batch_scanner.fetch_kline_sina_api",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("网络失败")),
        )
        assert _default_loader("600000") is None

    def test_multi_market_detect_routes(self):
        """detect_market 对三市场识别正确（跨市场 loader 的路由基础）。"""
        assert detect_market("600000").market == "CN"
        assert detect_market("000001").market == "CN"
        assert detect_market("00700").market == "HK"
        assert detect_market("09988").market == "HK"
        assert detect_market("AAPL").market == "US"
        assert detect_market("MSFT").market == "US"

    def test_detect_market_etf_prefixes(self):
        """回归：1 开头深市 ETF 必须判 sz（曾被误判 sh 导致取价失败）。"""
        assert detect_market("513310").symbol == "sh513310"   # 沪市ETF
        assert detect_market("159558").symbol == "sz159558"   # 深市ETF（曾被误判 sh）
        assert detect_market("600036").symbol == "sh600036"
        assert detect_market("000001").symbol == "sz000001"
        assert detect_market("200002").symbol == "sz200002"   # 深 B 股

    def test_default_loader_routes_by_market(self, monkeypatch):
        """回归：loader 按市场路由——CN 走新浪、HK 走腾讯、US 走 fetch_kline。

        修复前默认 loader 只走新浪 CN 接口，港股/美股批量扫描恒为空。
        """
        import pandas as pd

        fake = pd.DataFrame({
            "open": [10.0, 11.0], "high": [10.5, 11.5], "low": [9.5, 10.5],
            "close": [10.0, 11.0], "volume": [1e6, 1.2e6],
        })

        def fake_sina(info, **kw):
            assert info.market == "CN"
            return fake

        def fake_hk(info, **kw):
            assert info.market == "HK"
            return fake

        def fake_fetch(info, **kw):
            assert info.market == "US"
            return fake

        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.opportunity.batch_scanner.fetch_kline_sina_api",
            fake_sina,
        )
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.opportunity.batch_scanner.fetch_kline_hk_tencent",
            fake_hk,
        )
        monkeypatch.setattr(
            "quant_trading_system.stock_analysis.data_fetcher.fetch_kline", fake_fetch,
        )
        assert _default_loader("600000") is not None
        assert _default_loader("00700") is not None
        assert _default_loader("AAPL") is not None


class _FakeEngine:
    """按 code 返回预设决策的计划（0=BUY_NOW, 1=WATCH, 2=AVOID, raise=异常）。"""

    def __init__(self, decisions: dict, raise_on: set = None) -> None:
        self.decisions = decisions
        self.raise_on = raise_on or set()

    def analyze(self, code, name, df):
        if code in self.raise_on:
            raise RuntimeError("引擎异常")
        decision = self.decisions.get(code, DecisionState.WATCH)
        score = 90.0 if decision == DecisionState.BUY_NOW else 50.0
        return type("R", (), {
            "plan": TradingPlan(
                code=code, name=name, decision=decision,
                stock_score=70.0, opportunity_score=score,
                current_price=10.0, entry_low=9.5, entry_price=9.8, entry_high=10.2,
                stop_loss=9.3, target_1=11.0, target_2=12.0, target_3=13.0,
                risk_reward_1=2.5, risk_reward_2=4.0, position_percent=15.0,
            ),
        })()


class _FakeLoader:
    def __call__(self, code, name):
        return pd.DataFrame({"close": [10.0] * 100})


def _scanner(engine, **kw) -> OpportunityBatchScanner:
    return OpportunityBatchScanner(engine=engine, loader=_FakeLoader(), workers=4, **kw)


class TestBatchScan:
    def test_normalize_mixed_inputs(self):
        scanner = _scanner(_FakeEngine({}))
        codes = scanner._normalize(
            ["600000", {"code": "000001", "name": "平安"}, {"code": "600000"}, 123],
            name_map={"600000": "浦发"},
        )
        # 去重保序、str/dict 混合、int 代码转 str
        assert [c["code"] for c in codes] == ["600000", "000001"]
        assert codes[0]["name"] == "浦发"  # name_map 优先
        assert codes[1]["name"] == "平安"

    def test_fill_names_uses_code_table(self, monkeypatch):
        """A 股纯代码候选自动补全名称（模拟 akshare 代码表）。"""
        import pandas as pd

        fake_table = pd.DataFrame(
            [{"code": "600000", "name": "浦发银行"}, {"code": "601318", "name": "中国平安"}]
        )
        monkeypatch.setattr("akshare.stock_info_a_code_name", lambda: fake_table)
        # 清掉类缓存，确保走一次拉取
        OpportunityBatchScanner._NAME_TABLE = None
        OpportunityBatchScanner._NAME_TABLE_TS = 0.0

        scanner = _scanner(_FakeEngine({}))
        codes = scanner._normalize(["600000", "601318", "AAPL"], name_map=None)
        by_code = {c["code"]: c["name"] for c in codes}
        assert by_code["600000"] == "浦发银行"
        assert by_code["601318"] == "中国平安"
        # 非 A 股（美股）名称保持 code 兜底
        assert by_code["AAPL"] == "AAPL"

    def test_fill_names_failure_falls_back(self, monkeypatch):
        """代码表获取失败时不抛异常，名称回退为 code。"""
        def boom(*a, **kw):
            raise RuntimeError("网络失败")

        monkeypatch.setattr("akshare.stock_info_a_code_name", boom)
        OpportunityBatchScanner._NAME_TABLE = None
        OpportunityBatchScanner._NAME_TABLE_TS = 0.0

        scanner = _scanner(_FakeEngine({}))
        codes = scanner._normalize(["600000"], name_map=None)
        assert codes[0]["code"] == "600000"
        assert codes[0]["name"] == "600000"  # 兜底

    def test_scan_filters_avoid_and_sorts(self):
        engine = _FakeEngine({
            "A": DecisionState.BUY_NOW,   # 机会分 90
            "B": DecisionState.WATCH,     # 机会分 50
            "C": DecisionState.AVOID,     # 应被过滤
            "D": DecisionState.BUY_NOW,   # 机会分 90
        })
        res = _scanner(engine).scan(["A", "B", "C", "D"])
        codes = [p["code"] for p in res.plans]
        assert codes == ["A", "D", "B"]  # AVOID 过滤 + 按机会分降序
        assert all(p["decision"] != DecisionState.AVOID.value for p in res.plans)

    def test_engine_exception_recorded_as_failed(self):
        engine = _FakeEngine({"A": DecisionState.BUY_NOW}, raise_on={"B"})
        res = _scanner(engine).scan(["A", "B"])
        assert len(res.failed) == 1
        assert res.failed[0]["code"] == "B"
        assert "引擎异常" in res.failed[0]["error"]
        assert [p["code"] for p in res.plans] == ["A"]

    def test_empty_candidates(self):
        res = _scanner(_FakeEngine({})).scan([])
        assert res.plans == [] and res.items == [] and res.failed == []

    def test_to_dict(self):
        res = _scanner(_FakeEngine({"A": DecisionState.BUY_NOW})).scan(["A"])
        d = res.to_dict()
        assert set(d) == {"plans", "items", "failed", "elapsed"}
        assert isinstance(d["elapsed"], float)

    def test_min_score_filter(self):
        engine = _FakeEngine({"A": DecisionState.BUY_NOW, "B": DecisionState.WATCH})
        res = _scanner(engine, min_opportunity_score=80.0).scan(["A", "B"])
        assert [p["code"] for p in res.plans] == ["A"]  # B 机会分 50 < 80 被过滤

    def test_include_avoid_false_drops_items(self):
        engine = _FakeEngine({"A": DecisionState.AVOID, "B": DecisionState.BUY_NOW})
        res = _scanner(engine, include_avoid=False).scan(["A", "B"])
        assert [it.code for it in res.items] == ["B"]  # items 也不含 AVOID
        assert [p["code"] for p in res.plans] == ["B"]


class TestWithEmailIntegration:
    def test_plans_feed_email_template(self):
        """批量扫描结果可直接喂给邮件模板 trading_plans 参数。"""
        from quant_trading_system.stock_analysis.notifier import build_market_message

        engine = _FakeEngine({"A": DecisionState.BUY_NOW, "B": DecisionState.BUY_ON_PULLBACK})
        res = _scanner(engine).scan(["A", "B"])
        assert len(res.plans) == 2
        _, text, html = build_market_message("CN", [], trading_plans=res.plans)
        assert "今日机会 · 交易计划" in html
        assert "今日机会 · 交易计划" in text
