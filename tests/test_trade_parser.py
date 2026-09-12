"""TradeParser: parse broker 成交回报 texts (offline, no network)."""
from __future__ import annotations

from quant_trading_system.stock_analysis.trade_monitor import TradeParser


def _p(**kw):
    return TradeParser(kw)


def test_parse_buy_free_text():
    t = _p().parse("【同花顺】您的委托已成交：买入 600519 贵州茅台 100股 成交价1500.00元")
    assert t is not None
    assert t.side == "BUY"
    assert t.code == "600519"
    assert t.quantity == 100
    assert t.price == 1500.0


def test_parse_sell_structured_kv():
    text = (
        "成交提醒\n"
        "股票代码：    513310\n"
        "股票名称：    中韩半导体ETF\n"
        "交易方向：    买入，委托数量200股\n"
        "成交量：    已成交200股，已全部成交\n"
        "成交金额：    937.40元（成交价格：4.687元）\n"
    )
    t = _p().parse(text)
    assert t is not None
    assert t.side == "BUY"
    assert t.code == "513310"
    assert t.quantity == 200
    assert t.price == 4.687


def test_reject_non_trade_text():
    t = _p().parse("您好，您关注的贵州茅台最新研报已发布，点击查看详情")
    assert t is None


def test_parse_sell_with_hand_qty():
    # 「1手」应换算为 100 股
    t = _p().parse("【同花顺】您的买入委托已成交：创业板ETF(159915) 1手 1.85元")
    assert t is not None
    assert t.side == "BUY"
    assert t.code == "159915"
    assert t.quantity == 100


def test_parse_side_priority_over_code():
    # 同时出现买卖词时按多字关键词先后
    t = _p().parse("平安证券：您的卖出委托已成交 平安银行（000001）200股 12.50元")
    assert t is not None
    assert t.side == "SELL"
    assert t.code == "000001"


def test_app_hint_filter():
    # 配置 app_name_hint 后，不包含提示词的文本被忽略
    tp = _p(app_name_hint=["平安证券"])
    assert tp.parse("买入 600519 100股") is None
    assert tp.parse("平安证券：买入 600519 100股") is not None
