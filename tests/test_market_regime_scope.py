"""跨市场「市场状态」口径测试。

回归缺陷：``index_symbol`` 默认是上证指数，但调度器与看板详情页无论 CN/HK/US
都拿它取状态并传给机会引擎。后果有三层：

1. 港股/美股的评分与建议仓位被一个与自身无关的指数牵着走；
2. 看板「列表扫描」已按市场中性化、「单票详情」没有 —— 同一只票两处结论不一致；
3. 回测/邮件/看板三条链路口径不同，结论无法互相印证。

本文件锁死规则：**只有 A 股使用指数市场状态，其余市场一律中性**。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from quant_trading_system.stock_analysis.market import regime_for_market

_REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 纯函数：非 A 股恒中性
# --------------------------------------------------------------------------- #
class TestRegimeForMarket:
    @pytest.mark.parametrize("market", ["HK", "US", "hk", "us", "", "XX"])
    def test_non_cn_is_neutral_without_any_network(self, market, monkeypatch):
        """非 A 股必须直接返回中性，**且不触发指数请求**。"""
        import quant_trading_system.stock_analysis.market.index_data as mod

        def boom(*a, **kw):
            raise AssertionError("非 A 股不应请求指数行情")

        monkeypatch.setattr(mod, "fetch_market_context", boom)
        assert regime_for_market(market) == (None, 1.0)

    def test_cn_uses_index_context(self, monkeypatch):
        import quant_trading_system.stock_analysis.market.index_data as mod

        class _Regime:
            score = 72.5
            factor = 1.0

        monkeypatch.setattr(mod, "fetch_market_context",
                            lambda symbol, days=160: {"regime": _Regime()})
        assert regime_for_market("CN") == (72.5, 1.0)

    def test_cn_default_market_when_blank(self, monkeypatch):
        """空字符串按 A 股处理（与 ``str(market or "CN")`` 的历史默认一致）。"""
        import quant_trading_system.stock_analysis.market.index_data as mod

        class _Regime:
            score = 60.0
            factor = 0.75

        monkeypatch.setattr(mod, "fetch_market_context",
                            lambda symbol, days=160: {"regime": _Regime()})
        assert regime_for_market("") == (60.0, 0.75)

    def test_cn_failure_degrades_to_neutral(self, monkeypatch):
        """指数拿不到时必须降级中性，不能抛异常中断整轮扫描。"""
        import quant_trading_system.stock_analysis.market.index_data as mod

        def boom(*a, **kw):
            raise RuntimeError("网络失败")

        monkeypatch.setattr(mod, "fetch_market_context", boom)
        assert regime_for_market("CN") == (None, 1.0)

    def test_cn_missing_regime_degrades_to_neutral(self, monkeypatch):
        import quant_trading_system.stock_analysis.market.index_data as mod

        monkeypatch.setattr(mod, "fetch_market_context", lambda symbol, days=160: {})
        assert regime_for_market("CN") == (None, 1.0)

    def test_passes_index_symbol_through(self, monkeypatch):
        import quant_trading_system.stock_analysis.market.index_data as mod

        seen = {}

        class _Regime:
            score = 50.0
            factor = 1.0

        def fake(symbol, days=160):
            seen["symbol"] = symbol
            return {"regime": _Regime()}

        monkeypatch.setattr(mod, "fetch_market_context", fake)
        regime_for_market("CN", "sh000300")
        assert seen["symbol"] == "sh000300"


# --------------------------------------------------------------------------- #
# 调用方：三个入口都必须走同一条规则
# --------------------------------------------------------------------------- #
class TestCallSitesUseSharedRule:
    """源码级断言：防止有人改回「无条件套用上证指数」。"""

    def _src(self, rel: str) -> str:
        return (_REPO / rel).read_text(encoding="utf-8")

    def test_scheduler_uses_regime_for_market(self):
        src = self._src("stock_analysis/scheduler.py")
        assert "regime_for_market(" in src
        # 不允许再直接拿 index_symbol 去 fetch_market_context 并传给引擎
        assert "fetch_market_context(" not in src

    def test_dashboard_list_path_neutralises_non_cn(self):
        src = self._src("dashboard/pages/0_opportunity.py")
        assert 'cn_regime = regime_score if market == "CN" else None' in src
        assert 'cn_factor = market_factor if market == "CN" else 1.0' in src

    def test_dashboard_detail_path_neutralises_non_cn(self):
        src = self._src("dashboard/pages/0_opportunity.py")
        assert 'if info.market != "CN":' in src
        assert "regime_score, market_factor = None, 1.0" in src

    def test_dashboard_custom_scan_gates_on_all_cn(self):
        src = self._src("dashboard/pages/0_opportunity.py")
        assert '_all_cn = all(detect_market(c).market == "CN" for c in codes)' in src
