"""Strategy registry — create strategies by name for configs / CLI / grid search.

Example::

    from quant_trading_system.strategy import create_strategy, list_strategies

    s = create_strategy("ma_cross", symbols=["600000"], fast=5, slow=20)
    print(list_strategies())
"""
from __future__ import annotations

from typing import Any, Callable, Type

from .base import Strategy
from .mean_reversion import BollingerBandStrategy
from .ml_strategy import MLStrategy
from .multi_factor import MultiFactorStrategy
from .trend_following import MovingAverageCrossStrategy, TurtleBreakoutStrategy

STRATEGY_REGISTRY: dict[str, Type[Strategy]] = {
    "ma_cross": MovingAverageCrossStrategy,
    "moving_average_cross": MovingAverageCrossStrategy,
    "turtle": TurtleBreakoutStrategy,
    "turtle_breakout": TurtleBreakoutStrategy,
    "bollinger": BollingerBandStrategy,
    "bollinger_band": BollingerBandStrategy,
    "multi_factor": MultiFactorStrategy,
    "ml": MLStrategy,
}


def register_strategy(name: str, cls: Type[Strategy], *, overwrite: bool = False) -> None:
    key = name.strip().lower()
    if key in STRATEGY_REGISTRY and not overwrite:
        raise ValueError(f"strategy already registered: {key}")
    STRATEGY_REGISTRY[key] = cls


def list_strategies() -> list[str]:
    return sorted(set(STRATEGY_REGISTRY.keys()))


def create_strategy(name: str, symbols: list[str], **params: Any) -> Strategy:
    key = name.strip().lower()
    if key not in STRATEGY_REGISTRY:
        known = ", ".join(list_strategies())
        raise KeyError(f"unknown strategy {name!r}; known: {known}")
    cls = STRATEGY_REGISTRY[key]
    return cls(symbols, **params)
