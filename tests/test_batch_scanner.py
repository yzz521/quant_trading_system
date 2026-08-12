"""V2 批量机会扫描器单元测试（纯离线）。

用 FakeEngine 注入确定性 TradingPlan，直接验证过滤/排序/失败降级逻辑，
不依赖合成数据形态。
"""
from __future__ import annotations

import pandas as pd
from quant_trading_system.stock_analysis.opportunity import OpportunityBatchScanner
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState, TradingPlan


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
