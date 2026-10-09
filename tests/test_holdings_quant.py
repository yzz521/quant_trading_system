"""Holdings quant: already-held action mapping (offline)."""
from __future__ import annotations

import pandas as pd
import pytest
from quant_trading_system.stock_analysis.holdings_quant import (
    apply_live_prices,
    cached_block,
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


def test_cached_block_exposes_write_time(tmp_path):
    p = tmp_path / "holdings_quant.json"
    save_market_cache("CN", "2026-09-16", [{"code": "601398"}], path=p)
    blk = cached_block("CN", "2026-09-16", path=p)
    assert blk["items"][0]["code"] == "601398"
    assert blk.get("at")  # 落盘时间可追溯，展示层据此提示数据时点
    assert cached_block("CN", "2026-09-17", path=p) == {}


# --------------------------------------------------------------------------- #
# 现价口径：同一行里「现价」与「盈亏%」必须同源（回归 2026-09-16 的线上问题）
# 症状：持仓表用腾讯实时价，持仓量化用日K末根；新浪日K盘中不含当日 bar，
#       于是同一屏里同一只票出现 40.56 / 41.11 两个价格。
# --------------------------------------------------------------------------- #
def test_live_price_overrides_kline_price():
    """实时价必须压过 plan（K 线末根）里的现价。"""
    r = interpret_holding_action(_plan(current_price=41.10), {"pnl_pct": 2.85})
    assert r["current_price"] == 41.10  # 无实时价时退回 K 线
    r2 = interpret_holding_action(_plan(current_price=41.10), {"pnl_pct": 2.85},
                                  live_price=40.66)
    assert r2["current_price"] == 40.66


def test_position_realtime_price_beats_kline():
    """position 里带实时价（compute_pnl 注入）时也要压过 K 线末根。"""
    pos = {"code": "600036", "cost_price": 39.63, "current_price": 40.66}
    r = interpret_holding_action(_plan(current_price=41.10), pos)
    assert r["current_price"] == 40.66
    assert r["pnl_pct"] == pytest.approx(2.60, abs=0.01)


def test_price_and_pnl_same_source():
    """有现价与成本价时，盈亏% 必须用同一个现价重算，而不是取 zone 里的旧值。"""
    pos = {"code": "601398", "cost_price": 8.0, "current_price": 8.4}
    zone = {"pnl_pct": -3.0, "stop_loss": 7.8}  # 故意给一个矛盾的旧盈亏
    r = interpret_holding_action(_plan(current_price=8.13), pos, zone)
    assert r["current_price"] == 8.4
    assert r["pnl_pct"] == pytest.approx(5.0, abs=0.01)


def test_no_price_keeps_legacy_pnl():
    """拿不到现价/成本价时保持旧行为（zone 的 pnl_pct 优先）。"""
    r = interpret_holding_action(_plan(), {"pnl_pct": 5}, {"pnl_pct": 7})
    assert r["pnl_pct"] == pytest.approx(7.0)


def test_apply_live_prices_rewrites_only_price_fields():
    """缓存复用场景：刷新现价/盈亏%，但不许动 K 线算出来的技术位。"""
    items = [{
        "code": "600036", "cost_price": 39.63, "current_price": 41.10,
        "pnl_pct": 3.71, "stop_loss": 40.49, "zone_lo": 44.0, "target_1": 45.0,
        "as_of": "2026-09-16 10:57",
    }]
    apply_live_prices(items, {"600036": 40.66})
    it = items[0]
    assert it["current_price"] == 40.66
    assert it["pnl_pct"] == pytest.approx(2.60, abs=0.01)
    assert it["price_src"] == "live"
    assert it["as_of"] != "2026-09-16 10:57"
    assert it["stop_loss"] == 40.49 and it["zone_lo"] == 44.0 and it["target_1"] == 45.0


def test_apply_live_prices_tolerates_missing_price():
    items = [{"code": "600036", "cost_price": 39.63, "current_price": 41.10}]
    apply_live_prices(items, {})            # 快照为空 → 原样返回
    apply_live_prices(items, {"600036": None})  # 该票没取到 → 不动
    assert items[0]["current_price"] == 41.10


def test_holding_loader_prefers_today_bar(monkeypatch):
    """持仓量化默认 loader 必须选「末根含当日」的源；前复权源失败才回退。"""
    from quant_trading_system.stock_analysis import holdings_quant as hq

    called: list[str] = []

    def _fake_today(info, days=250):
        called.append("today")
        idx = pd.date_range("2026-06-01", periods=120, freq="D")
        close = [10.0 + (i % 7) * 0.1 for i in range(120)]
        return pd.DataFrame(
            {"open": close, "high": [c + 0.2 for c in close],
             "low": [c - 0.2 for c in close], "close": close,
             "volume": [1000.0 + i for i in range(120)]},
            index=idx,
        )

    monkeypatch.setattr(hq, "fetch_kline", _fake_today)
    monkeypatch.setattr(hq, "_default_loader",
                        lambda code, market="CN": called.append("fallback") or None)

    df = hq._holding_loader("601398", "CN")
    assert called == ["today"] and df is not None and len(df) == 120

    called.clear()
    monkeypatch.setattr(hq, "fetch_kline", lambda info, days=250: pd.DataFrame())
    hq._holding_loader("601398", "CN")
    assert called == ["fallback"]


def test_analyze_holdings_quant_uses_injected_price_and_loader(monkeypatch):
    """端到端（离线）：现价取注入的实时快照，K 线走注入的 loader。"""
    from types import SimpleNamespace

    from quant_trading_system.stock_analysis import holdings_quant as hq

    idx = pd.date_range("2026-06-01", periods=120, freq="D")
    df = pd.DataFrame(
        {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 1000.0},
        index=idx,
    )
    fake_plan = SimpleNamespace(
        current_price=8.13, stop_loss=7.9, target_1=9.0, entry_low=8.0,
        entry_high=8.5, decision="WATCH", meta={}, stock_score=60,
        opportunity_score=55, reasons=[], risks=[], name="工商银行",
    )
    engine = SimpleNamespace(analyze=lambda code, name, d: SimpleNamespace(plan=fake_plan))

    items = hq.analyze_holdings_quant(
        [{"code": "601398", "name": "工商银行", "market": "CN",
          "cost_price": 7.975, "quantity": 200}],
        engine=engine,
        loader=lambda code, market="CN": df,
        prices={"601398": 8.06},
        fetch_news=False,
    )
    assert len(items) == 1
    assert items[0]["current_price"] == 8.06          # 实时价，不是 K 线末根 8.13
    assert items[0]["pnl_pct"] == pytest.approx(1.07, abs=0.01)
    assert items[0]["price_src"] == "live"
    assert items[0]["as_of"]


# --------------------------------------------------------------------------- #
# 组合级风控块（接入邮件/看板的桥）
# --------------------------------------------------------------------------- #
def test_portfolio_risk_block_uses_equity_denominator():
    """有总资产时权重 = 市值/总资产（含现金），与单票上限同一把尺子。"""
    from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

    holdings = [
        {"code": "A", "name": "A", "quantity": 1000, "current_price": 10,
         "market": "CN"},                                   # 市值 10000
        {"code": "B", "name": "B", "quantity": 1000, "current_price": 10,
         "market": "CN"},                                   # 市值 10000
    ]
    block = portfolio_risk_block(holdings, total_equity=100_000)
    assert block is not None
    rep = block["report"]
    assert rep["n_holdings"] == 2
    assert rep["total_weight"] == pytest.approx(0.2)       # 各 10%，合计 20%
    assert rep["concentration"]["top1"] == pytest.approx(0.1)
    assert block["verdict"] == "正常"
    assert block["brake"] == pytest.approx(1.0)


def test_portfolio_risk_block_flags_industry_and_single():
    from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

    holdings = [
        {"code": "A", "name": "重仓", "quantity": 5000, "current_price": 10},
        {"code": "B", "name": "B", "quantity": 1000, "current_price": 10},
    ]
    block = portfolio_risk_block(holdings, total_equity=100_000,
                                 sector_map={"A": "白酒", "B": "白酒"})
    assert block is not None
    assert any("单票超限" in b for b in block["breaches"])
    assert any("行业超限" in b for b in block["breaches"])
    assert block["verdict"].startswith("超限")


def test_portfolio_risk_block_falls_back_to_relative_weights():
    from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

    block = portfolio_risk_block(
        [{"code": "A", "quantity": 300, "current_price": 10},
         {"code": "B", "quantity": 100, "current_price": 10}])
    assert block is not None
    assert block["report"]["total_weight"] == pytest.approx(1.0)


def test_portfolio_risk_block_returns_none_on_empty():
    from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

    assert portfolio_risk_block([]) is None
    assert portfolio_risk_block([{"code": ""}]) is None


def test_portfolio_risk_block_survives_bad_quantity():
    from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

    block = portfolio_risk_block(
        [{"code": "A", "quantity": "abc", "current_price": "x"},
         {"code": "B", "quantity": 100, "current_price": 10}])
    assert block is not None
    assert block["report"]["n_holdings"] == 1


def test_risk_block_renderers():
    from quant_trading_system.stock_analysis.holdings_quant import (
        portfolio_risk_block,
        risk_block_to_html,
        risk_block_to_text,
    )

    block = portfolio_risk_block(
        [{"code": "A", "name": "A", "quantity": 5000, "current_price": 10},
         {"code": "B", "name": "B", "quantity": 1000, "current_price": 10}],
        total_equity=100_000, sector_map={"A": "白酒", "B": "白酒"})
    txt = risk_block_to_text(block)
    assert "组合风控" in txt and "组合风控：" in txt
    html = risk_block_to_html(block)
    assert "有效持仓数" in html and "行业暴露" in html
    assert "超限" in html and "建议" in html
    # 空块必须安全
    assert risk_block_to_text(None) == ""
    assert "暂无" in risk_block_to_html(None)


def test_notifier_renders_portfolio_risk_section():
    from quant_trading_system.stock_analysis.notifier import build_market_message

    block = {"verdict": "注意", "summary": "组合风控：注意\n  ⚠️ 单票超限",
             "report": {"concentration": {"effective_n": 3.0, "top1": 0.5, "top3": 0.8},
                        "correlation": {"avg_corr": 0.62},
                        "var": {"var": 0.021, "cvar": 0.03},
                        "drawdown": {"current_drawdown": -0.06, "max_drawdown": -0.11,
                                     "brake": 0.75},
                        "by_industry": [{"industry": "白酒", "weight": 0.5, "n": 2,
                                         "over_limit": True}]},
             "breaches": ["单票超限"], "actions": ["降低权重"], "brake": 0.75,
             "as_of": "2026-09-24 10:30"}
    title, text, html = build_market_message("CN", holdings=[{"code": "A", "name": "A",
                                                              "quantity": 100,
                                                              "cost_price": 10,
                                                              "current_price": 11}],
                                             portfolio_risk=block)
    assert "组合风控" in text
    assert "组合风控" in html
    assert "75%" in html or "0.75" in html


def test_notifier_without_portfolio_risk_has_no_section():
    from quant_trading_system.stock_analysis.notifier import build_market_message

    _, text, html = build_market_message("CN", holdings=[])
    assert "组合风控" not in text
    assert "组合风控" not in html
