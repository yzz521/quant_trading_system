"""板块轮动（Sector Rotation）单元测试（纯离线，monkeypatch akshare）。"""
from __future__ import annotations

import pandas as pd
import pytest
from quant_trading_system.stock_analysis.sector import (
    fetch_sector_rank,
    get_stock_sectors,
    sector_factor,
)


@pytest.fixture(autouse=True)
def _clear_sector_cache():
    """每个用例前重置模块级缓存（避免用例间污染）。"""
    from quant_trading_system.stock_analysis import sector as _s

    _s._rank_cache = []
    _s._rank_ts = 0.0
    _s._map_cache = {}
    _s._map_ts = 0.0
    yield


def _sina_sector_spot() -> pd.DataFrame:
    """新浪 49 行业快照（板块/涨跌幅/总成交额）。"""
    return pd.DataFrame([
        {"label": "s1", "板块": "医疗服务", "涨跌幅": 3.99, "总成交额": 2.0e10},
        {"label": "s2", "板块": "化学制药", "涨跌幅": 1.38, "总成交额": 1.8e10},
        {"label": "s3", "板块": "电力", "涨跌幅": 0.25, "总成交额": 1.5e10},
        {"label": "s4", "板块": "通信设备", "涨跌幅": -0.5, "总成交额": 1.6e10},
        {"label": "s5", "板块": "酿酒行业", "涨跌幅": -1.2, "总成交额": 1.2e10},
    ])


class TestFetchSectorRank:
    def test_rank_sorted_by_strength(self, monkeypatch):
        monkeypatch.setattr("akshare.stock_sector_spot", lambda indicator=None: _sina_sector_spot())
        rank = fetch_sector_rank("CN")
        assert len(rank) == 5
        strengths = [s["strength"] for s in rank]
        assert strengths == sorted(strengths, reverse=True)
        # 涨得最多的板块应该强度最高
        assert rank[0]["name"] == "医疗服务"
        assert 0 <= rank[0]["strength"] <= 100

    def test_non_cn_returns_empty(self):
        assert fetch_sector_rank("US") == []

    def test_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "akshare.stock_sector_spot",
            lambda indicator=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert fetch_sector_rank("CN") == []


class TestGetStockSectors:
    def test_mapping_built(self, monkeypatch):
        spot = pd.DataFrame([
            {"label": "new_blhy", "板块": "玻璃行业"},
            {"label": "new_nj", "板块": "酿酒行业"},
        ])
        details = {
            "new_blhy": pd.DataFrame({"code": ["600176", "600184"], "name": ["中国巨石", "光电股份"]}),
            "new_nj": pd.DataFrame({"code": ["600519", "000858"], "name": ["贵州茅台", "五粮液"]}),
        }
        monkeypatch.setattr("akshare.stock_sector_spot", lambda indicator: spot)
        monkeypatch.setattr("akshare.stock_sector_detail", lambda sector: details[sector])

        m = get_stock_sectors()
        assert m["600519"] == "酿酒行业"
        assert m["000858"] == "酿酒行业"
        assert m["600176"] == "玻璃行业"
        assert len(m) == 4

    def test_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "akshare.stock_sector_spot",
            lambda indicator: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert get_stock_sectors() == {}


class TestSectorFactor:
    def test_hit_returns_strength(self):
        rank = [{"name": "医疗服务", "strength": 96.4}]
        assert sector_factor("医疗服务", rank) == 96.4

    def test_miss_returns_neutral(self):
        rank = [{"name": "医疗服务", "strength": 96.4}]
        assert sector_factor("酿酒行业", rank) == 50.0

    def test_empty_inputs_neutral(self):
        assert sector_factor(None, None) == 50.0
        assert sector_factor("医疗服务", []) == 50.0
