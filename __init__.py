"""Quantitative Trading System

A modular, event-driven quantitative trading framework supporting multi-market
(A-shares / US / HK / futures / crypto) backtesting, paper trading and live
execution through a unified event bus.

Quick start::

    from quant_trading_system import BacktestEngine, BacktestConfig
    from quant_trading_system.data import SyntheticDataSource, BarFeed
    from quant_trading_system.strategy import MovingAverageCrossStrategy

    ds = SyntheticDataSource()
    df = ds.get_history("DEMO", "2022-01-01", "2024-12-31")
    feed = BarFeed({"DEMO": df})
    engine = BacktestEngine(BacktestConfig(initial_capital=1_000_000))
    engine.add_strategy(MovingAverageCrossStrategy(["DEMO"], fast=5, slow=20))
    portfolio = engine.run(feed)
    print(portfolio.equity)
"""
from .core import EventEngine
from .backtest import BacktestEngine, BacktestConfig
from .analytics import PerformanceReport, compute_metrics

__version__ = "0.1.0"
__all__ = [
    "EventEngine",
    "BacktestEngine",
    "BacktestConfig",
    "PerformanceReport",
    "compute_metrics",
    "__version__",
]
