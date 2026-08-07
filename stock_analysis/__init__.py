"""Stock analysis toolkit — diagnose real stocks across A-share / US / HK.

Public API::

    from quant_trading_system.stock_analysis import StockDiagnoser, StockScanner
    result = StockDiagnoser().diagnose("600000")   # A-share
    result = StockDiagnoser().diagnose("AAPL")     # US
    result = StockDiagnoser().diagnose("00700")    # HK
    print(result.summary, result.score, result.rating)

    hits = StockScanner().scan(["600000","000001"], ["多头排列","放量"])
"""
from .data_fetcher import detect_market, fetch_kline, fetch_name, MarketInfo
from .indicators import add_all_indicators
from .patterns import scan_signals
from .diagnosis import StockDiagnoser, DiagnosisResult
from .scanner import StockScanner, ScanHit, PRESETS
from .notifier import Notifier, build_market_message
from .scheduler import MarketScheduler
from .holdings import Holdings
from .report import StockReport

__all__ = [
    "StockDiagnoser",
    "DiagnosisResult",
    "StockScanner",
    "ScanHit",
    "PRESETS",
    "Notifier",
    "MarketScheduler",
    "build_market_message",
    "StockReport",
    "detect_market",
    "fetch_kline",
    "fetch_name",
    "MarketInfo",
    "add_all_indicators",
    "scan_signals",
]
from .holdings_bridge import apply_holdings_to_portfolio, portfolio_to_holdings_rows, snapshot_compare  # noqa: F401
from .universe import make_universe, symbols_from_scan_hits, symbols_from_holdings  # noqa: F401
