"""Data layer: market data sources, caching, and the backtest bar feed.

The data layer is deliberately split into small, swappable pieces so that
adding a new vendor (e.g. Bloomberg, Wind, Tushare Pro) only requires
implementing :class:`DataSource`.
"""
from .data_source import DataSource, AssetClass
from .cache import DiskCache
from .akshare_source import AkShareSource
from .yfinance_source import YFinanceSource
from .synthetic_source import SyntheticDataSource
from .feed import BarFeed, DataFeed

__all__ = [
    "DataSource",
    "AssetClass",
    "DiskCache",
    "AkShareSource",
    "YFinanceSource",
    "SyntheticDataSource",
    "BarFeed",
    "DataFeed",
]
