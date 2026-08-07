"""Execution layer for live & paper trading.

The same event-driven core that powers backtests also drives live trading.
The only differences are:

* the data feed is real-time (ticks/bars streamed from the broker);
* orders go to a real exchange instead of a simulated matcher.

Concrete brokers are thin adapters around vendor SDKs. They are intentionally
left as **skeletons** — wire them up with your own credentials and the vendor
SDK before going live. Paper trading works out of the box with no credentials.
"""
from .broker_base import LiveBroker, LiveFeed, LiveBrokerConfig
from .paper_broker import PaperBroker
from .ctp_broker import CTPBroker
from .ibkr_broker import IBKRBroker
from .binance_broker import BinanceBroker
from .live_engine import LiveTradingEngine, LiveConfig
from .live_bar_poller import LiveBarPoller

__all__ = [
    "LiveBroker",
    "LiveFeed",
    "LiveBrokerConfig",
    "PaperBroker",
    "CTPBroker",
    "IBKRBroker",
    "BinanceBroker",
    "LiveTradingEngine",
    "LiveConfig",
    "LiveBarPoller",
]
