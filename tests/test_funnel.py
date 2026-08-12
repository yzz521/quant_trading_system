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


def test_spot_snapshot_has_l2_columns(monkeypatch):
    import akshare

    raw = pd.DataFrame([{
        "代码": "600001", "名称": "x", "最新价": 10.0, "涨跌幅": 1.0,
        "成交量": 1000, "成交额": 1e8, "总市值": 1e10, "流通市值": 8e9,
        "市盈率-动态": 20.0, "换手率": 1.5, "市净率": 2.0,
    }])
    monkeypatch.setattr(akshare, "stock_zh_a_spot", lambda: raw)
    out = funnel_mod.fetch_spot_snapshot()
    assert out.loc[0, "total_cap_yi"] == 100.0
    assert out.loc[0, "float_cap_yi"] == 80.0
    assert out.loc[0, "pe"] == 20.0
    assert out.loc[0, "turnover"] == 1.5


def test_run_l2_fallback_spot_when_tencent_down(monkeypatch):
    spot = pd.DataFrame([{
        "code": "600001", "name": "x", "close": 10.0, "pct_chg": 1.0,
        "volume": 1e7, "amount": 8e7,
        "total_cap_yi": 100.0, "pe": 20.0, "turnover": 1.0,
    }])
    monkeypatch.setattr(funnel_mod, "fetch_spot_snapshot", lambda: spot)
    monkeypatch.setattr(funnel_mod, "fetch_tencent_quotes", lambda codes, batch=50: None)

    def fake_tech(self, codes, name_map=None):
        return [{
            "code": c, "name": (name_map or {}).get(c), "market": "CN",
            "close": 10.0, "score": 50,
        } for c in codes]

    def fake_l4(self, tech, quote_map, holdings_mgr, held):
        return tech

    monkeypatch.setattr(FunnelScanner, "_technical_pass", fake_tech)
    monkeypatch.setattr(FunnelScanner, "stage_l4", fake_l4)

    out = _scanner().run()
    l2 = out["stages"][1]
    assert l2["after"] == 1  # 腾讯挂掉时用新浪快照保住 L2
    assert out["hits"][0]["code"] == "600001"
    assert out["elapsed"] >= 0


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
    # 关闭新闻层，避免测试依赖真实网络新闻（CI 上 600009 会命中风险关键词）
    f = _scanner(top_n=10, main_net_bonus=10, news_enabled=False)

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


def test_news_risk_pass_sentiment():
    f = _scanner(
        news_enabled=True,
        news_penalty=15,
        news_risk_keywords=["减持", "诉讼"],
    )
    items = [{"code": "600001", "score": 50}]

    def fake_news(code, days=7, limit=20, sources=None):
        return [{"title": "600001 遭股东减持", "url": "http://x", "ctime": 0}]

    out = f._news_risk_pass(items, news_fetcher=fake_news)
    nr = out[0]["news_risks"][0]
    assert nr["sentiment"] < 0
    assert nr["sentiment_label"] == "偏空"


def test_run_industry_cap_applied(monkeypatch):
    spot = pd.DataFrame([
        {"code": "600001", "name": "a", "close": 10.0, "pct_chg": 1.0,
         "volume": 1e7, "amount": 8e7, "total_cap_yi": 100.0, "pe": 20.0, "turnover": 1.0},
        {"code": "600002", "name": "b", "close": 10.0, "pct_chg": 1.0,
         "volume": 1e7, "amount": 8e7, "total_cap_yi": 100.0, "pe": 20.0, "turnover": 1.0},
    ])
    monkeypatch.setattr(funnel_mod, "fetch_spot_snapshot", lambda: spot)
    monkeypatch.setattr(funnel_mod, "fetch_tencent_quotes", lambda codes, batch=50: None)
    monkeypatch.setattr(
        funnel_mod, "fetch_industry_map",
        lambda: {"600001": "银行", "600002": "银行"},
    )

    def fake_tech(self, codes, name_map=None):
        return [{
            "code": c, "name": (name_map or {}).get(c), "market": "CN",
            "close": 10.0, "score": 50,
        } for c in codes]

    def fake_l4(self, tech, quote_map, holdings_mgr, held):
        return tech

    monkeypatch.setattr(FunnelScanner, "_technical_pass", fake_tech)
    monkeypatch.setattr(FunnelScanner, "stage_l4", fake_l4)
    out = _scanner(industry_diversify={"enabled": True, "max_per_industry": 1}).run()
    assert [h["code"] for h in out["hits"]] == ["600001"]  # 同行业最多 1 只


def test_news_risk_pass_disabled():
    f = _scanner(news_enabled=False)
    items = [{"code": "600001", "score": 50}]
    out = f._news_risk_pass(items)
    assert out[0]["score"] == 50
    assert "news_risks" not in out[0]
