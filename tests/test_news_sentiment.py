"""新闻标题情绪规则单测。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.news_sentiment import (
    score_and_label,
    score_text,
    sentiment_label,
)


def test_score_positive_negative():
    assert score_text("公司业绩预增，订单创新高") > 0
    assert score_text("公司遭立案调查，业绩预亏") < 0
    assert score_text("召开股东大会") == 0.0


def test_labels():
    assert sentiment_label(0.5) == "偏多"
    assert sentiment_label(-0.5) == "偏空"
    assert sentiment_label(0.0) == "中性"
    assert sentiment_label(0.1) == "中性"  # 低于阈值


def test_score_and_label_shape():
    r = score_and_label("公司披露减持计划")
    assert set(r) == {"sentiment", "sentiment_label"}
    assert r["sentiment_label"] == "偏空"
