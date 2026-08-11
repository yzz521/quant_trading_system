"""payload 富化（行业/筹码）离线单测：monkeypatch 数据源，不打网络。"""
from __future__ import annotations

from quant_trading_system.stock_analysis import data_fetcher
from quant_trading_system.stock_analysis.vibe_bridge import enrich_payload


def _payload() -> dict:
    return {
        "source": "gp_assistant",
        "market": "CN",
        "holdings": [{"code": "600036", "name": "招商银行", "quantity": 100}],
        "candidates": [{"code": "600519", "name": "贵州茅台", "score": 45}],
    }


def test_enrich_payload(monkeypatch):
    monkeypatch.setattr(
        data_fetcher, "fetch_industry_map",
        lambda: {"600036": "银行", "600519": "白酒"},
    )
    monkeypatch.setattr(
        data_fetcher, "fetch_chip_distribution",
        lambda code: {"profit_ratio": 55.0, "avg_cost": 38.0},
    )
    p = enrich_payload(_payload())
    h = p["holdings"][0]
    assert h["industry"] == "银行"
    assert h["industry_category"] == "金融"
    assert h["chip"]["profit_ratio"] == 55.0
    c = p["candidates"][0]
    assert c["industry"] == "白酒"
    assert c["industry_category"] == "防御"


def test_enrich_payload_failure_keeps_payload(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network blocked")

    monkeypatch.setattr(data_fetcher, "fetch_industry_map", boom)
    monkeypatch.setattr(data_fetcher, "fetch_chip_distribution", boom)
    p = enrich_payload(_payload(), with_chip=True)
    assert p["holdings"][0].get("industry") is None
    assert p["holdings"][0].get("chip") is None
    assert p["candidates"][0].get("industry") is None
