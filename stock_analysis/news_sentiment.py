"""新闻标题情绪评分（规则词典，离线可用）。

借鉴 ashare-analyzer 的“新闻情绪智能体”思路，但用轻量规则实现：
命中积极词 +1、消极词 -1，归一化到 [-1, 1]。
"""
from __future__ import annotations


POSITIVE_WORDS: tuple[str, ...] = (
    "增长", "上涨", "新高", "突破", "中标", "签约", "回购", "增持", "预增",
    "扭亏", "盈利", "利好", "获批", "放量", "创新", "改善", "超预期", "回暖",
    "扩张", "提价", "涨价", "合作", "战略", "订单", "产能", "投产",
)

NEGATIVE_WORDS: tuple[str, ...] = (
    "下跌", "亏损", "减持", "质押", "冻结", "处罚", "违规", "立案", "调查",
    "诉讼", "仲裁", "退市", "风险警示", "问询", "监管函", "关注函", "预亏",
    "商誉减值", "合同纠纷", "终止", "暂停", "解禁", "爆雷", "违约", "利空",
    "下滑", "萎缩", "计提", "召回",
)


def score_text(text: str) -> float:
    """标题情绪分：[-1, 1]，正=偏多，负=偏空。"""
    if not text:
        return 0.0
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


def sentiment_label(score: float, threshold: float = 0.15) -> str:
    """情绪标签：偏多 / 偏空 / 中性。"""
    if score >= threshold:
        return "偏多"
    if score <= -threshold:
        return "偏空"
    return "中性"


def score_and_label(text: str, threshold: float = 0.15) -> dict:
    score = score_text(text)
    return {"sentiment": score, "sentiment_label": sentiment_label(score, threshold)}
