"""Holdings quant: already-held action mapping (offline)."""
from __future__ import annotations

from quant_trading_system.stock_analysis.holdings_quant import (
    cached_items,
    interpret_holding_action,
    save_market_cache,
)


def _plan(**kw):
    base = {
        "current_price": 11.0,
        "stop_loss": 9.5,
        "target_1": 13.0,
        "entry_low": 10.5,
        "entry_high": 11.2,
        "decision": "WATCH",
        "meta": {},
    }
    base.update(kw)
    return base


def test_stop_triggers_sell():
    r = interpret_holding_action(_plan(current_price=9.4), {"pnl_pct": 5})
    assert r["action"] == "SELL"


def test_severe_news_reduces():
    r = interpret_holding_action(
        _plan(meta={"information": {"severe": True, "grade": "风险"}}),
        {"pnl_pct": 8},
    )
    assert r["action"] == "REDUCE"


def test_bearish_tech_reduces():
    r = interpret_holding_action(
        _plan(meta={"technical": {"grade": "C", "tags": ["均线空头", "MACD空头"]}}),
        {"pnl_pct": 2},
    )
    assert r["action"] == "REDUCE"


def test_avoid_stays_hold_no_add():
    r = interpret_holding_action(_plan(decision="AVOID"), {"pnl_pct": 3})
    assert r["action"] == "HOLD"
    assert "不加仓" in r["note"]


def test_add_on_healthy_pullback():
    r = interpret_holding_action(
        _plan(
            current_price=10.8,
            entry_low=10.5,
            entry_high=11.0,
            meta={"technical": {"grade": "A", "tags": ["均线多头"]}},
        ),
        {"pnl_pct": 6},
    )
    assert r["action"] == "ADD"


def test_deep_loss_hold_not_sell():
    r = interpret_holding_action(
        _plan(current_price=11.0, stop_loss=8.0),
        {"pnl_pct": -25},
        {"regime": "deep_loss", "pnl_pct": -25, "stop_loss": 8.0},
    )
    assert r["action"] == "HOLD"
    assert "深套" in r["note"]


def test_buy_now_is_not_a_ticket():
    r = interpret_holding_action(_plan(decision="BUY_NOW"), {"pnl_pct": 12})
    assert r["action"] in ("HOLD", "ADD", "REDUCE", "SELL")
    assert r["action"] != "BUY_NOW"


def test_avoid_enum_stays_hold():
    from types import SimpleNamespace
    from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState

    plan = SimpleNamespace(
        current_price=11.0, stop_loss=9.5, target_1=13.0,
        entry_low=10.5, entry_high=11.2, decision=DecisionState.AVOID, meta={},
    )
    r = interpret_holding_action(plan, {"pnl_pct": 3})
    assert r["action"] == "HOLD"
    assert "不加仓" in r["note"]


def test_engine_default_skips_news():
    from quant_trading_system.stock_analysis.opportunity.opportunity_engine import OpportunityEngine
    assert OpportunityEngine().fetch_news is False


def test_cache_path_uses_data_dir(tmp_path, monkeypatch):
    from quant_trading_system.stock_analysis.holdings_quant import cache_path
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("QTS_DATA_DIR", str(cfg))
    assert cache_path() == tmp_path / "results" / "holdings_quant.json"


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "holdings_quant.json"
    save_market_cache("CN", "2026-09-03", [{"code": "1"}], path=p)
    assert cached_items("CN", "2026-09-03", path=p)[0]["code"] == "1"
    assert cached_items("CN", "2026-09-04", path=p) is None
