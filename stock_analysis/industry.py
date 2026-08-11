"""行业分类：周期/金融/成长/防御/其他。

移植自 ashare-analyzer（MIT, Copyright 2026 zwldarren）的 industry 模块：
归一化 + 别名解析 + 关键词匹配；另补充成长/防御类别（本项目扩展）。
"""
from __future__ import annotations

import re
from enum import Enum


class IndustryCategory(Enum):
    CYCLICAL = "cyclical"    # 周期
    FINANCIAL = "financial"  # 金融
    GROWTH = "growth"        # 成长
    DEFENSIVE = "defensive"  # 防御
    OTHER = "other"          # 其他


CATEGORY_LABEL = {
    IndustryCategory.CYCLICAL: "周期",
    IndustryCategory.FINANCIAL: "金融",
    IndustryCategory.GROWTH: "成长",
    IndustryCategory.DEFENSIVE: "防御",
    IndustryCategory.OTHER: "其他",
}


# --- 周期性行业（移植自 ashare-analyzer） ---
CYCLICAL_INDUSTRIES: set[str] = {
    "有色金属", "小金属", "贵金属", "能源金属",
    "煤炭", "石油石化", "油气开采", "油服工程",
    "钢铁", "化工", "化纤", "建材", "建筑材料", "水泥", "玻璃", "造纸",
    "工程机械", "重型机械", "船舶制造", "航运", "港口", "航运港口", "航空机场",
    "汽车", "汽车整车", "汽车零部件",
    "房地产", "房地产开发",
    # 新浪行业板块名（周期类）
    "纺织", "服装鞋类", "陶瓷", "塑料制品", "公路桥梁", "发电设备",
    "交通运输", "酒店旅游", "物资外贸", "摩托车",
}


# --- 金融行业（移植自 ashare-analyzer） ---
FINANCIAL_INDUSTRIES: set[str] = {
    "银行", "商业银行",
    "保险", "保险公司",
    "证券", "券商", "证券公司", "投资银行",
    "多元金融", "金融控股", "信托", "期货", "租赁", "金融科技", "互联网金融",
    "金融",  # 新浪行业板块名「金融行业」归一化后
}


# --- 成长行业（本项目扩展） ---
GROWTH_INDUSTRIES: set[str] = {
    "半导体", "电子", "消费电子", "计算机设备", "软件开发", "通信设备",
    "通信服务", "互联网服务", "游戏", "人工智能", "机器人", "光伏设备",
    "电池", "风电设备", "电网设备", "医疗器械", "生物制品", "创新药",
    "航空装备", "航天装备", "军工电子",
    # 新浪行业板块名（成长类）
    "传媒娱乐", "电器", "仪器仪表",
}


# --- 防御行业（本项目扩展） ---
DEFENSIVE_INDUSTRIES: set[str] = {
    "食品饮料", "白酒", "乳品", "调味发酵品", "休闲食品", "饮料乳品",
    "医药商业", "中药", "化学制药", "医疗服务", "医药",
    "公用事业", "电力", "燃气", "水务", "环保",
    "种植业", "养殖业", "农产品加工", "农业",
    "电信运营",
}


INDUSTRY_ALIASES: dict[str, str] = {
    "证券公司": "证券", "投资银行": "证券", "券商信托": "证券", "证券投资": "证券",
    "银行业": "银行",
    "保险业": "保险",
    "有色": "有色金属", "有色金属冶炼": "有色金属", "有色金属加工": "有色金属",
    "稀有金属": "小金属", "稀土": "小金属", "稀土永磁": "小金属",
    "锂电": "小金属", "锂": "小金属", "锂矿": "小金属", "钴": "小金属",
    "能源金属行业": "能源金属", "锂资源": "能源金属",
    "煤炭开采": "煤炭", "煤炭采选": "煤炭", "煤炭行业": "煤炭",
    "黑色金属": "钢铁", "钢铁冶炼": "钢铁", "钢铁行业": "钢铁", "普钢": "钢铁", "特钢": "钢铁",
    "化学制品": "化工", "化学原料": "化工", "化工行业": "化工", "基础化工": "化工", "精细化工": "化工",
    "化学纤维": "化纤",
    "建材": "建筑材料", "建材行业": "建筑材料",
    "水泥制造": "水泥", "水泥行业": "水泥",
    "汽车制造": "汽车", "汽车行业": "汽车", "乘用车": "汽车", "商用车": "汽车",
    "地产": "房地产", "地产行业": "房地产", "房地产开发经营": "房地产",
    "石油": "石油石化", "石油开采": "石油石化", "石油行业": "石油石化", "石化": "石油石化",
    "黄金": "贵金属", "白银": "贵金属",
    "港口航运": "航运港口",
    "航空运输": "航空机场", "机场航运": "航空机场",
    # 新浪行业板块名（49 个）映射到规范行业
    "建筑建材": "建筑材料", "机械行业": "工程机械",
    "电子器件": "电子", "电子信息": "电子", "家电行业": "电器",
    "飞机制造": "航空装备", "生物制药": "生物制品", "医疗器械行业": "医疗器械",
    "酿酒行业": "白酒", "食品行业": "食品饮料", "医药生物": "医药",
    "农林牧渔": "农业", "供水供气": "公用事业", "农药化肥": "化工",
    "玻璃行业": "玻璃", "船舶制造": "船舶制造",
    "化纤行业": "化纤", "纺织行业": "纺织", "纺织机械": "纺织",
    "服装鞋类": "服装鞋类", "陶瓷行业": "陶瓷", "塑料制品": "塑料制品",
    "公路桥梁": "公路桥梁", "发电设备": "发电设备", "交通运输": "交通运输",
    "酒店旅游": "酒店旅游", "物资外贸": "物资外贸", "摩托车": "摩托车",
    "传媒娱乐": "传媒娱乐", "电器行业": "电器", "仪器仪表": "仪器仪表",
    # 成长/防御别名（本项目扩展）
    "电子元件": "电子", "电子化学品": "电子", "光学光电子": "电子",
    "半导体材料": "半导体", "集成电路": "半导体",
    "白酒Ⅱ": "白酒", "食品加工": "食品饮料", "饮料制造": "饮料乳品",
    "化学制药Ⅱ": "化学制药", "生物医药": "生物制品", "医药生物": "医药",
    "电力行业": "电力", "燃气行业": "燃气",
    # 归一化去后缀后的别名（“酿酒行业”先归一为“酿酒”再查别名）
    "酿酒": "白酒", "家电": "电器", "食品": "食品饮料",
    "机械": "工程机械", "汽车": "汽车",
}


INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "有色金属": ["铜", "铝", "锌", "铅", "镍", "锡", "钨", "钼"],
    "小金属": ["稀土", "锂", "锂矿", "钴", "锗", "镁"],
    "能源金属": ["锂盐", "镍钴"],
    "贵金属": ["黄金", "白银", "铂金", "钯金"],
    "煤炭": ["焦煤", "动力煤", "无烟煤", "焦炭"],
    "钢铁": ["特钢", "不锈钢", "普钢", "钢材", "铁矿石"],
    "化工": ["化肥", "农药", "聚氨酯", "氯碱", "纯碱", "钛白粉", "染料", "涂料", "有机硅"],
    "建筑材料": ["水泥", "玻璃", "管材", "防水材料", "保温材料"],
    "汽车": ["整车", "零部件", "新能源车", "电动车", "汽车电子", "轮胎"],
    "房地产": ["地产", "物业", "园区开发", "商业地产"],
    "石油石化": ["油气", "炼化", "油服", "石化"],
    "航运港口": ["航运", "港口", "海运", "集装箱", "散货"],
    "航空机场": ["航空", "机场", "民航", "空运"],
    "证券": ["券商", "投行"],
    "银行": ["商业银行", "股份制银行", "城商行"],
    "半导体": ["芯片", "晶圆", "封测", "存储"],
    "电子": ["PCB", "面板", "连接器", "被动元件"],
    "通信设备": ["光模块", "基站", "光纤"],
    "计算机设备": ["服务器", "算力"],
    "软件开发": ["SaaS", "信创"],
    "游戏": ["手游", "网络游戏"],
    "光伏设备": ["光伏", "硅片", "组件"],
    "电池": ["动力电池", "储能", "锂电"],
    "医疗器械": ["体外诊断", "耗材"],
    "创新药": ["单抗", "ADC", "GLP-1"],
    "食品饮料": ["白酒", "啤酒", "乳品", "饮料"],
    "中药": ["中成药"],
    "医药商业": ["连锁药房"],
    "公用事业": ["发电", "水电", "核电", "电网"],
    "电力": ["火电", "绿电"],
    "燃气": ["城市燃气"],
    "养殖业": ["生猪", "禽养殖", "饲料"],
    "种植业": ["种业", "粮食"],
}


COMMON_INDUSTRY_SUFFIXES: list[str] = [
    "行业", "板块", "概念", "指数", "一级行业", "二级行业", "三级行业",
]


def normalize_industry_name(industry: str) -> str:
    """去空白与常见后缀，如 '有色金属行业' → '有色金属'。"""
    if not industry:
        return ""
    normalized = industry.strip()
    suffixes = sorted(COMMON_INDUSTRY_SUFFIXES, key=len, reverse=True)
    for suffix in suffixes:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[:-len(suffix)]
            break
    return normalized


def resolve_alias(industry: str) -> str:
    """别名 → 规范名；无别名返回原值。"""
    if not industry:
        return ""
    return INDUSTRY_ALIASES.get(industry, industry)


def classify_industry(industry: str) -> str | None:
    """返回规范行业名；无法识别返回 None。

    匹配顺序：归一化精确匹配 → 别名 → 关键词包含匹配。
    """
    if not industry:
        return None
    normalized = normalize_industry_name(industry)
    if not normalized:
        return None
    canonical = resolve_alias(normalized)
    if canonical in CYCLICAL_INDUSTRIES or canonical in FINANCIAL_INDUSTRIES \
            or canonical in GROWTH_INDUSTRIES or canonical in DEFENSIVE_INDUSTRIES:
        return canonical
    for name, keywords in INDUSTRY_KEYWORDS.items():
        if any(k in normalized for k in keywords):
            return name
    return None


def get_industry_category(industry: str) -> IndustryCategory:
    """行业名 → 大类（周期/金融/成长/防御/其他）。"""
    canonical = classify_industry(industry)
    if canonical is None:
        return IndustryCategory.OTHER
    if canonical in CYCLICAL_INDUSTRIES:
        return IndustryCategory.CYCLICAL
    if canonical in FINANCIAL_INDUSTRIES:
        return IndustryCategory.FINANCIAL
    if canonical in GROWTH_INDUSTRIES:
        return IndustryCategory.GROWTH
    if canonical in DEFENSIVE_INDUSTRIES:
        return IndustryCategory.DEFENSIVE
    return IndustryCategory.OTHER


def category_label(category: IndustryCategory) -> str:
    return CATEGORY_LABEL.get(category, "其他")


def _strip_code(value: str) -> str:
    m = re.search(r"(\d{6})", str(value or ""))
    return m.group(1) if m else ""
