"""Stock analysis toolkit (main-v3 精简版) — 三块核心功能。

Public API::

    from quant_trading_system.stock_analysis import (
        MarketInfo, detect_market, fetch_kline, fetch_name, add_all_indicators,
        Notifier, build_market_message, MarketScheduler, Holdings,
    )

功能范围：今日计划（机会引擎/回测/AI/市场状态）、我的持仓、持仓量化、持仓卖出/加仓参考、
每日邮件推送（持仓 + 资金 + 持仓量化 + 今日机会 + 卖出参考）、
实时盯盘（快轨：盘中级预警，见 realtime.RealtimeWatcher，默认关闭）。
"""
from .data_fetcher import MarketInfo, detect_market, fetch_kline, fetch_name
from .holdings import Holdings
from .indicators import add_all_indicators
from .notifier import Notifier, build_market_message
from .realtime import Alert, RealtimeWatcher, RuleEngine, in_session
from .scheduler import MarketScheduler
from .screener import screen_candidates
from .sector import fetch_sector_rank, get_stock_sectors, sector_factor

__all__ = [
    "MarketInfo",
    "detect_market",
    "fetch_kline",
    "fetch_name",
    "add_all_indicators",
    "Notifier",
    "build_market_message",
    "MarketScheduler",
    "RealtimeWatcher",
    "RuleEngine",
    "Alert",
    "in_session",
    "Holdings",
    "screen_candidates",
    "fetch_sector_rank",
    "get_stock_sectors",
    "sector_factor",
]
