"""Backtest layer: simulated broker, execution handler and the engine that
wires everything together."""
from .broker import SimulatedBroker
from .execution_handler import ExecutionHandler
from .engine import BacktestEngine, BacktestConfig
from .optimizer import grid_search, slice_feed, walk_forward, walk_forward_summary

__all__ = [
    "SimulatedBroker",
    "ExecutionHandler",
    "BacktestEngine",
    "BacktestConfig",
    "grid_search",
    "slice_feed",
    "walk_forward",
    "walk_forward_summary",
]
