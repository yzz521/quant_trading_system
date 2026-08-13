"""V2 邮件模板「今日机会 · 交易计划」区块测试。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.notifier import _plans_html, build_market_message


def _plan() -> dict:
    return {
        "code": "600000",
        "name": "测试股",
        "decision": "BUY_ON_PULLBACK",
        "stock_score": 88.0,
        "opportunity_score": 82.0,
        "current_price": 12.36,
        "entry_low": 11.80,
        "entry_price": 11.95,
        "entry_high": 12.10,
        "stop_loss": 11.35,
        "target_1": 13.20,
        "target_2": 14.50,
        "target_3": 15.80,
        "risk_reward_1": 2.08,
        "risk_reward_2": 4.25,
        "position_percent": 20.0,
    }


class TestPlansHtml:
    def test_empty(self):
        assert "<p class='neutral'>暂无</p>" in _plans_html([])

    def test_renders_row(self):
        html = _plans_html([_plan()])
        assert "今日机会" not in html  # helper 只渲染表格
        assert "600000" in html
        assert "测试股" in html  # 名称
        assert "11.80~12.10" in html
        assert "13.20/14.50" in html
        assert "RR=2.08" in html  # 紧凑格式
        assert "20.0%" in html

    def test_decision_emoji(self):
        html = _plans_html([_plan()])
        assert "🟢 BUY_ON_PULLBACK" in html
        html_avoid = _plans_html([{**_plan(), "decision": "AVOID", "position_percent": None}])
        assert "⛔ AVOID" in html_avoid
        assert ">" in html_avoid  # 仓位 — 显示为破折号
        assert "<td>—</td>" in html_avoid

    def test_missing_fields_tolerated(self):
        html = _plans_html([{"code": "1", "decision": "WATCH"}])
        assert "<tr>" in html
        assert "RR=—</td>" in html  # risk_reward 缺失兜底为 RR=—
        assert "RR=None" not in html
        # 名称缺失时回退为代码（不显示空单元格）
        assert "<td>1<br>" in html


class TestBuildMarketMessage:
    def test_plans_block_present(self):
        _, text, html = build_market_message("CN", [], trading_plans=[_plan()])
        assert "🎯 今日机会 · 交易计划" in html
        assert "🎯 今日机会 · 交易计划" in text
        assert "测试股(600000) BUY_ON_PULLBACK" in text
        assert "11.80~12.10" in html
        assert "仓位20.0%" in text

    def test_plans_optional(self):
        # 不传 trading_plans（旧调用方）不报错、无区块
        _, _, html = build_market_message("CN", [])
        assert "今日机会" not in html
        # 显式 None 也不报错
        _, _, html2 = build_market_message("CN", [], trading_plans=None)
        assert "今日机会" not in html2

    def test_avoid_plan_no_position_in_text(self):
        _, text, _ = build_market_message(
            "CN", [], trading_plans=[{**_plan(), "decision": "AVOID", "position_percent": None}]
        )
        assert "AVOID" in text
        assert "仓位" not in text
