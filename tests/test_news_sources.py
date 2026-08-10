"""漏斗 L4 新闻多源（东财/雪球/新浪兜底）离线单测：monkeypatch 各源，不打网络。"""
from __future__ import annotations

from quant_trading_system.stock_analysis import data_fetcher


def test_merge_dedup_and_order(monkeypatch):
    monkeypatch.setattr(data_fetcher, "_news_eastmoney", lambda code, limit: [
        {"title": "600000 减持公告", "url": "http://em/1", "ctime": 100},
        {"title": "600000 半年报披露", "url": "http://em/2", "ctime": 90},
    ])
    monkeypatch.setattr(data_fetcher, "_news_xueqiu", lambda code, limit: [
        {"title": "600000 减持公告", "url": "http://xq/1", "ctime": 95},  # 与东财重复
        {"title": "600000 机构调研", "url": "", "ctime": 0},
    ])
    monkeypatch.setattr(data_fetcher, "_news_sina", lambda code, days, limit: [
        {"title": "600000 午间公告", "url": "http://sina/1", "ctime": 80},
    ])
    out = data_fetcher.fetch_stock_news("600000", days=7, limit=20)
    assert [n["title"] for n in out] == [
        "600000 减持公告", "600000 半年报披露", "600000 午间公告", "600000 机构调研",
    ]
    assert out[0]["url"] == "http://em/1"  # 保留先到源


def test_sources_filter(monkeypatch):
    calls = {"em": 0, "xq": 0, "sina": 0}

    def em(code, limit):
        calls["em"] += 1
        return []

    def xq(code, limit):
        calls["xq"] += 1
        return []

    def sina(code, days, limit):
        calls["sina"] += 1
        return []

    monkeypatch.setattr(data_fetcher, "_news_eastmoney", em)
    monkeypatch.setattr(data_fetcher, "_news_xueqiu", xq)
    monkeypatch.setattr(data_fetcher, "_news_sina", sina)
    data_fetcher.fetch_stock_news("600000", sources=["xueqiu"])
    assert calls == {"em": 0, "xq": 1, "sina": 0}


def test_single_source_failure_skipped(monkeypatch):
    def boom(code, limit):
        raise RuntimeError("blocked")

    monkeypatch.setattr(data_fetcher, "_news_eastmoney", boom)
    monkeypatch.setattr(data_fetcher, "_news_xueqiu", lambda code, limit: [
        {"title": "xq 新闻", "url": "", "ctime": 1},
    ])
    monkeypatch.setattr(data_fetcher, "_news_sina", lambda code, days, limit: [
        {"title": "sina 新闻", "url": "", "ctime": 2},
    ])
    out = data_fetcher.fetch_stock_news("600000")
    assert [n["title"] for n in out] == ["sina 新闻", "xq 新闻"]
