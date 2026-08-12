"""V2 AI 分析师单元测试（不打网络）。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.ai.ai_analyst import _fallback_explain, explain_plan
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState, TradingPlan


def _plan(**over) -> TradingPlan:
    base = dict(
        code="600000",
        name="测试股",
        decision=DecisionState.BUY_NOW,
        stock_score=80.0,
        opportunity_score=75.0,
        current_price=12.0,
        entry_low=11.80,
        entry_price=11.95,
        entry_high=12.10,
        stop_loss=11.35,
        target_1=13.20,
        target_2=14.50,
        target_3=15.80,
        risk_reward_1=2.08,
        risk_reward_2=4.25,
        position_percent=20.0,
        holding_period="5~20 个交易日",
        confidence=0.87,
        reasons=["测试理由"],
        risks=["跌破止损需离场"],
        invalidate_condition="收盘跌破 11.35 即逻辑失效",
    )
    base.update(over)
    return TradingPlan(**base)


class TestFallback:
    def test_buy_now(self):
        text = _fallback_explain(_plan())
        assert "已进入入场区间" in text
        assert "## 为什么值得关注" in text
        assert "## 现在能不能买" in text
        assert "## 入场逻辑" in text
        assert "## 关键风险" in text
        assert "## 失效条件" in text
        assert "11.95" in text  # 入场价

    def test_avoid(self):
        text = _fallback_explain(_plan(decision=DecisionState.AVOID, entry_price=None))
        assert "不建议" in text
        assert "不具备清晰入场条件" in text

    def test_buy_on_pullback(self):
        text = _fallback_explain(_plan(decision=DecisionState.BUY_ON_PULLBACK))
        assert "等回踩" in text

    def test_hold(self):
        text = _fallback_explain(_plan(decision=DecisionState.HOLD))
        assert "已持有" in text

    def test_sell(self):
        text = _fallback_explain(_plan(decision=DecisionState.SELL))
        assert "卖出" in text

    def test_watch(self):
        text = _fallback_explain(_plan(decision=DecisionState.WATCH))
        assert "观察" in text

    def test_none_plan(self):
        assert "无交易计划" in _fallback_explain(None)


class TestExplainPlan:
    def test_disabled_uses_fallback(self):
        """AI 未启用 → 直接走兜底，不调网络。"""
        cfg = {"ai": {"enabled": False}}
        text = explain_plan(_plan(), notify_cfg=cfg)
        assert "已进入入场区间" in text

    def test_no_key_uses_fallback(self):
        cfg = {"ai": {"enabled": True, "api_key": ""}}
        text = explain_plan(_plan(decision=DecisionState.WATCH), notify_cfg=cfg)
        assert "观察" in text

    def test_none_config_uses_default_path(self, monkeypatch):
        """未传配置时尝试默认路径（config/notify.yaml），应返回字符串而非异常。"""
        import quant_trading_system.stock_analysis.ai.ai_analyst as mod

        # 无论默认配置是否启用 AI，都 mock 掉网络调用，只验证「不抛异常、返回字符串」
        monkeypatch.setattr(mod, "chat_completion", lambda *a, **kw: None)
        text = explain_plan(_plan())
        assert isinstance(text, str)
        assert text

    def test_enabled_calls_llm(self, monkeypatch):
        cfg = {"ai": {"enabled": True, "api_key": "sk-test", "model": "m", "base_url": "https://x", "timeout": 1, "max_tokens": 100, "temperature": 0.2}}
        import quant_trading_system.stock_analysis.ai.ai_analyst as mod

        called = {}

        def fake_chat(messages, **kw):
            called["n"] = len(messages)
            called["system"] = messages[0]["role"]
            assert messages[1]["content"]  # user 内容非空
            return "AI 解读内容"

        monkeypatch.setattr(mod, "chat_completion", fake_chat)
        text = explain_plan(_plan(), notify_cfg=cfg)
        assert text == "AI 解读内容"
        assert called["n"] == 2

    def test_llm_failure_falls_back(self, monkeypatch):
        cfg = {"ai": {"enabled": True, "api_key": "sk-test", "model": "m", "base_url": "https://x", "timeout": 1, "max_tokens": 100, "temperature": 0.2}}
        import quant_trading_system.stock_analysis.ai.ai_analyst as mod

        monkeypatch.setattr(mod, "chat_completion", lambda *a, **kw: None)
        text = explain_plan(_plan(), notify_cfg=cfg)
        assert "已进入入场区间" in text  # 回退兜底
