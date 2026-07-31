"""Strategy layer.

A strategy is a stateful object that receives market data and emits
:class:`SignalEvent`. It deliberately does **not** size positions or talk to
the broker — that is the job of the execution handler / risk manager, so the
same strategy runs unchanged across backtest and live.
"""
from .base import Strategy, StrategyContext
from .trend_following import MovingAverageCrossStrategy, TurtleBreakoutStrategy
from .mean_reversion import BollingerBandStrategy
from .multi_factor import MultiFactorStrategy
from .ml_strategy import MLStrategy

__all__ = [
    "Strategy",
    "StrategyContext",
    "MovingAverageCrossStrategy",
    "TurtleBreakoutStrategy",
    "BollingerBandStrategy",
    "MultiFactorStrategy",
    "MLStrategy",
]
