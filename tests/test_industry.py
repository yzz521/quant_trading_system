"""行业分类离线单测（移植自 ashare-analyzer 的逻辑）。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.funnel import FunnelScanner
from quant_trading_system.stock_analysis.industry import (
    IndustryCategory,
    category_label,
    classify_industry,
    get_industry_category,
    normalize_industry_name,
    resolve_alias,
)


def test_normalize_and_alias():
    assert normalize_industry_name("有色金属行业") == "有色金属"
    assert normalize_industry_name("  证券板块  ") == "证券"
    assert resolve_alias("证券公司") == "证券"
    assert resolve_alias("券商") == "券商"  # 券商本身是规范名
    assert resolve_alias("煤炭开采") == "煤炭"


def test_classify():
    assert classify_industry("有色金属") == "有色金属"
    assert classify_industry("银行") == "银行"
    assert classify_industry("锂电池材料") == "小金属"
    assert classify_industry("计算机") is None


def test_category():
    assert get_industry_category("有色金属") is IndustryCategory.CYCLICAL
    assert get_industry_category("银行") is IndustryCategory.FINANCIAL
    assert get_industry_category("半导体") is IndustryCategory.GROWTH
    assert get_industry_category("食品饮料") is IndustryCategory.DEFENSIVE
    assert get_industry_category("计算机") is IndustryCategory.OTHER
    assert category_label(IndustryCategory.CYCLICAL) == "周期"


def test_sina_board_names():
    """新浪 49 板块名应正确归类（金融行业此前误判为其他）。"""
    assert get_industry_category("金融行业") is IndustryCategory.FINANCIAL
    assert get_industry_category("钢铁行业") is IndustryCategory.CYCLICAL
    assert get_industry_category("建筑建材") is IndustryCategory.CYCLICAL
    assert get_industry_category("酿酒行业") is IndustryCategory.DEFENSIVE
    assert get_industry_category("医药生物") is IndustryCategory.DEFENSIVE
    assert get_industry_category("电子器件") is IndustryCategory.GROWTH
    assert get_industry_category("半导体") is IndustryCategory.GROWTH


def test_industry_cap():
    f = FunnelScanner({})
    items = [
        {"code": "600001", "name": "a"},
        {"code": "600002", "name": "b"},
        {"code": "600003", "name": "c"},
        {"code": "600004", "name": "d"},
        {"code": "600005", "name": "e"},
    ]
    ind_map = {
        "600001": "银行", "600002": "银行", "600003": "银行",
        "600004": "证券", "600005": "银行",
    }
    out = f._apply_industry_cap(items, ind_map, max_per_industry=2)
    codes = [i["code"] for i in out]
    assert codes == ["600001", "600002", "600004"]  # 银行最多2只，证券1只
    assert out[0]["industry"] == "银行"
    # 未知行业不设限
    out2 = f._apply_industry_cap(items, {"600001": "银行"}, max_per_industry=1)
    assert len(out2) == 5
