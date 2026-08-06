"""Funnel scanner 离线单元测试（不打网络）。"""
from __future__ import annotations

import pandas as pd

from quant_trading_system.stock_analysis import funnel as funnel_mod
from quant_trading_system.stock_analysis.data_fetcher import _batches
from quant_trading_system.stock_analysis.funnel import DEFAULTS, FunnelScanner
from quant_trading_system.stock_analysis.scanner import ScanHit


def _scanner(**overrides) -> FunnelScanner:
    cfg = dict(DEFAULTS)
    cfg.update(overrides)
    return FunnelScanner(cfg)


def test_stage_l1_filters_st_zero_amount():
    f = _scanner(min_amount=50_000_000)
    df = pd.DataFrame([
        {"code": "600000", "name": "浦发银行", "close": 10.0, "pct_chg": 1.0, "volume": 1e7, "amount": 8e7},
        {"code": "600001", "name": "ST康美", "close": 2.0, "pct_chg": 0.0, "volume": 1e6, "amount": 8e7},
        {"code": "600002", "name": "*ST海投", "close": 2.0, "pct_chg": 0.0, "volume": 1e6, "amount": 8e7},
        {"code": "600003", "name": "某退市整理", "close": 1.0, "pct_chg": 0.0, "volume": 1e6, "amount": 8e7},
        {"code": "600004", "name": "停牌股", "close": 0.0, "pct_chg": 0.0, "volume": 0, "amount": 0},
        {"code": "600005", "name": "低成交", "close": 5.0, "pct_chg": 0.0, "volume": 1e6, "amount": 1e7},
        {"code": "600006", "name": "边界成交", "close": 5.0, "pct_chg": 0.0, "volume": 1e6, "amount": 5e7},
    ])
    assert f.stage_l1(df) == ["600000", "600006"]


def test_stage_l1_none_returns_empty():
    assert _scanner().stage_l1(None) == []


def test_stage_l2_filters_cap_pe_turnover():
    f = _scanner(min_market_cap=50, pe_min=0, pe_max=100, min_turnover=0.3)
    df = pd.DataFrame([
        {"code": "600001", "name": "a", "total_cap_yi": 100.0, "pe": 20.0, "turnover": 1.0},
        {"code": "600002", "name": "b", "total_cap_yi": 49.0, "pe": 20.0, "turnover": 1.0},
        {"code": "600003", "name": "c", "total_cap_yi": 50.0, "pe": 20.0, "turnover": 1.0},
        {"code": "600004", "name": "d", "total_cap_yi": 100.0, "pe": None, "turnover": 1.0},
        {"code": "600005", "name": "e", "total_cap_yi": 100.0, "pe": -5.0, "turnover": 1.0},
        {"code": "600006", "name": "f", "total_cap_yi": 100.0, "pe": 100.0, "turnover": 1.0},
        {"code": "600007", "name": "g", "total_cap_yi": 100.0, "pe": 99.9, "turnover": 0.29},
        {"code": "600008", "name": "h", "total_cap_yi": 100.0, "pe": 99.9, "turnover": 0.3},
    ])
    out = f.stage_l2(df)
    assert out["code"].tolist() == ["600001", "600003", "600008"]


def test_l3_sorts_and_limits():
    f = _scanner(l3_limit=3)

    def fake_evaluator(code, resolved):
        return ScanHit(
            code=code, name=code, market="CN", close=1.0, change_pct=0.0,
            score=int(code[-2:]), matched=["多头排列(新晋)"],
            matched_days={"多头排列(新晋)": 1}, signals=[],
        )

    codes = [f"6000{i:02d}" for i in range(5)]
    out = f._technical_pass(codes, evaluator=fake_evaluator)
    assert [h.code for h in out] == ["600004", "600003", "600002"]


def test_l4_bonus_dedup_topn(monkeypatch):
    f = _scanner(top_n=10, main_net_bonus=10)

    def fake_mf(code):
        return {"main_net": 1e8 if code == "600009" else None, "date": "2026-08-05"}

    monkeypatch.setattr(funnel_mod, "fetch_money_flow", fake_mf)
    hits = [
        ScanHit(
            code=f"6000{i:02d}", name="x", market="CN", close=10.0, change_pct=0.0,
            score=50, matched=["放量"], matched_days={"放量": 1}, signals=[],
        )
        for i in range(15)
    ]
    quote_map = {
        f"6000{i:02d}": {"total_cap_yi": 100.0, "pe": 20.0, "turnover": 1.0}
        for i in range(15)
    }
    out = f.stage_l4(hits, quote_map, holdings_mgr=None, held_codes={"600001"})
    codes = [h["code"] for h in out]
    assert len(out) == 10
    assert "600001" not in codes
    bonus = next(h for h in out if h["code"] == "600009")
    assert bonus["score"] == 60
    assert bonus["main_net"] == 1e8
    assert bonus["market_cap"] == 100.0
    scores = [h["score"] for h in out]
    assert scores == sorted(scores, reverse=True)


def test_batches_chunking():
    assert [len(x) for x in _batches(list(range(10)), 3)] == [3, 3, 3, 1]


def test_news_risk_pass_penalizes_and_flags():
    f = _scanner(
        news_enabled=True,
        news_penalty=15,
        news_risk_keywords=["诉讼", "减持", "退市"],
    )
    items = [
        {"code": "600001", "score": 50},
        {"code": "600002", "score": 50},
    ]

    def fake_news(code, days=7, limit=20):
        if code == "600001":
            return [{"title": "关于重大诉讼的公告", "url": "http://x", "ctime": 0}]
        return [{"title": "三季度业绩说明会", "url": "http://y", "ctime": 0}]

    out = f._news_risk_pass(items, news_fetcher=fake_news)
    by = {it["code"]: it for it in out}
    assert by["600001"]["score"] == 35
    assert by["600001"]["risk_flag"] is True
    assert by["600001"]["news_risks"][0]["keywords"] == ["诉讼"]
    assert by["600002"]["score"] == 50
    assert by["600002"]["news_risks"] == []
    assert not by["600002"].get("risk_flag")


def test_news_risk_pass_disabled():
    f = _scanner(news_enabled=False)
    items = [{"code": "600001", "score": 50}]
    out = f._news_risk_pass(items)
    assert out[0]["score"] == 50
    assert "news_risks" not in out[0]
