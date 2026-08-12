"""指数行情获取 —— 为市场状态（Regime/Risk）提供真实数据。

上证指数 sh000001 / 沪深300 sh000300 等在新浪日K接口可直接拉取。
失败时返回 None（调用方降级为中性市场，不阻塞主流程）。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ...utils import get_logger
from ..data_fetcher import MarketInfo, fetch_kline_sina_api
from ..indicators import add_all_indicators

log = get_logger("MarketIndex")

# 常用指数：symbol → 名称
INDEX_MAP = {
    "sh000001": "上证指数",
    "sh000300": "沪深300",
    "sz399001": "深证成指",
    "sh000905": "中证500",
}


def fetch_index_kline(symbol: str = "sh000001", days: int = 160) -> Optional[pd.DataFrame]:
    """拉取指数日K并附加全部指标（供 detect_market_regime / calc_market_risk 使用）。

    Args:
        symbol: 指数代码（如 sh000001 / sh000300）。
        days: 交易日数（≥120 以便指标预热）。

    Returns:
        已加指标的 DataFrame；失败返回 None。
    """
    try:
        info = MarketInfo(code=symbol, market="CN", symbol=symbol, name=INDEX_MAP.get(symbol, symbol))
        raw = fetch_kline_sina_api(info, days=days)
        if raw is None or len(raw) < 60:
            log.warning("指数 %s 数据不足（%s 行）", symbol, 0 if raw is None else len(raw))
            return None
        return add_all_indicators(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("指数 %s 获取失败: %s", symbol, e)
        return None


def fetch_market_context(symbol: str = "sh000001", days: int = 160) -> dict:
    """拉取指数并返回市场状态上下文（regime + risk + breadth=中性）。

    供 scheduler / dashboard 一次取齐市场状态。任一环节失败返回中性市场，
    不抛出异常。
    """
    from .market_regime import detect_market_regime
    from .market_risk import calc_market_risk

    df = fetch_index_kline(symbol, days=days)
    if df is None:
        log.info("指数 %s 不可用，返回中性市场状态", symbol)
        return {
            "regime": detect_market_regime(None),
            "risk": calc_market_risk(None),
            "index": symbol,
            "index_name": INDEX_MAP.get(symbol, symbol),
        }
    regime = detect_market_regime(df)
    risk = calc_market_risk(df)
    return {
        "regime": regime,
        "risk": risk,
        "index": symbol,
        "index_name": INDEX_MAP.get(symbol, symbol),
        "close": float(df["close"].iloc[-1]) if len(df) else None,
    }
