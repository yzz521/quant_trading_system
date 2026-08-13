"""Stock analysis toolkit (main-v3 精简版) — 三块核心功能。

Public API::

    from quant_trading_system.stock_analysis import (
        MarketInfo, detect_market, fetch_kline, fetch_name, add_all_indicators,
        Notifier, build_market_message, MarketScheduler, Holdings,
    )

功能范围：今日计划（机会引擎/回测/AI/市场状态）、我的持仓、持仓卖出/加仓参考、
每日邮件推送（今日机会 + 持仓 + 卖出参考）。
"""
from .data_fetcher import MarketInfo, detect_market, fetch_kline, fetch_name
from .holdings import Holdings
from .indicators import add_all_indicators
from .notifier import Notifier, build_market_message
from .scheduler import MarketScheduler
from .screener import screen_candidates

__all__ = [
    "MarketInfo",
    "detect_market",
    "fetch_kline",
    "fetch_name",
    "add_all_indicators",
    "Notifier",
    "build_market_message",
    "MarketScheduler",
    "Holdings",
    "screen_candidates",
]
