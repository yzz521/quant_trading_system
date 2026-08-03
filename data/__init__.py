"""Data layer: market data sources, caching, and the backtest bar feed."""
from .data_source import DataSource, AssetClass
from .cache import DiskCache
from .akshare_source import AkShareSource
from .yfinance_source import YFinanceSource
from .synthetic_source import SyntheticDataSource
from .fallback_source import FallbackDataSource
from .local_parquet_source import LocalParquetSource
from .feed import BarFeed, DataFeed
from .quality import validate_ohlcv, assert_ohlcv, normalize_columns

__all__ = [
    "DataSource",
    "AssetClass",
    "DiskCache",
    "AkShareSource",
    "YFinanceSource",
    "SyntheticDataSource",
    "FallbackDataSource",
    "LocalParquetSource",
    "BarFeed",
    "DataFeed",
    "validate_ohlcv",
    "assert_ohlcv",
    "normalize_columns",
]
