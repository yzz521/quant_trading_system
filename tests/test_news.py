"""Information-layer classification (offline). Network fetch is fail-open."""
from __future__ import annotations

from quant_trading_system.stock_analysis.news import classify_title, rate_headlines
from quant_trading_system.stock_analysis.scoring.stock_score import calc_stock_score
from quant_trading_system.stock_analysis.indicators import add_all_indicators
import numpy as np
import pandas as pd


def test_classify_risk_wins_over_catalyst():
    kind, kw, sev = classify_title("公司回购股份同时大股东减持")
    assert kind == "risk"
    assert kw == "减持"
    assert sev == "medium"


def test_classify_severe():
    kind, kw, sev = classify_title("因涉嫌信披违规被立案调查")
    assert kind == "risk" and sev == "severe"
    assert kw in ("立案", "调查", "违规")


def test_classify_catalyst():
    kind, kw, sev = classify_title("控股股东增持计划暨股权激励")
    assert kind == "catalyst"
    assert kw in ("增持", "股权激励")


def test_classify_ignores_spaced_media_title():
    kind, kw, _ = classify_title("大 股 东 减 持 预 披 露")
    assert kind == "risk" and kw == "减持"


def test_rate_empty_is_neutral():
    snap = rate_headlines([])
    assert snap.score == 50.0
    assert snap.grade == "中性"
    assert snap.risks == [] and not snap.severe


def test_rate_dedupes_same_keyword():
    items = [
        {"title": "股东减持预披露（一）", "source": "公告"},
        {"title": "股东减持预披露（二）", "source": "公告"},
    ]
    snap = rate_headlines(items)
    assert len(snap.risks) == 1
    assert snap.risks[0]["keyword"] == "减持"
    assert snap.score < 50


def test_rate_catalyst_raises_score():
    snap = rate_headlines([{"title": "发布股份回购报告书"}])
    assert snap.score > 50
    assert snap.grade == "偏多"


def test_stock_score_duplicate_news_counts_once():
    rng = np.random.default_rng(1)
    close = 10 + np.cumsum(rng.normal(0.02, 0.1, 80))
    df = add_all_indicators(pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6,
    }))
    one = [{"title": "减持A", "keyword": "减持"}]
    two = one + [{"title": "减持B", "keyword": "减持"}]
    a = calc_stock_score(df, news_risks=one)
    b = calc_stock_score(df, news_risks=two)
    assert a.components["risk"] == b.components["risk"]


def test_stock_score_catalyst_lifts_risk_component():
    rng = np.random.default_rng(2)
    close = 10 + np.cumsum(rng.normal(0.02, 0.1, 80))
    df = add_all_indicators(pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6,
    }))
    base = calc_stock_score(df)
    up = calc_stock_score(df, news_catalysts=[{"title": "回购", "keyword": "回购"}])
    assert up.components["risk"] > base.components["risk"]
