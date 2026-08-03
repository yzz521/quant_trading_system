"""Parameter grid search and walk-forward backtest helpers.

These utilities wrap :class:`BacktestEngine` without changing strategy code.
They are research tools — results can overfit; always inspect OOS windows.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import pandas as pd

from ..analytics import compute_metrics
from ..data.feed import BarFeed
from .engine import BacktestConfig, BacktestEngine

_METRIC_KEYS = (
    "sharpe",
    "total_return",
    "max_drawdown",
    "annual_return",
    "calmar",
    "win_rate",
    "final_equity",
)


@dataclass
class TrialResult:
    params: dict[str, Any]
    metrics: dict[str, float]
    equity: float


def slice_feed(feed: BarFeed, start=None, end=None) -> BarFeed:
    """Return a new BarFeed limited to ``[start, end]`` (inclusive)."""
    data = {}
    for sym, df in feed.data.items():
        part = df
        if start is not None:
            part = part[part.index >= pd.Timestamp(start)]
        if end is not None:
            part = part[part.index <= pd.Timestamp(end)]
        if not part.empty:
            data[sym] = part
    return BarFeed(data, frequency=feed.frequency)


def _run_once(strategy, feed: BarFeed, config: BacktestConfig | None) -> dict[str, float]:
    engine = BacktestEngine(config or BacktestConfig())
    engine.add_strategy(strategy)
    portfolio = engine.run(feed)
    return compute_metrics(portfolio)


def grid_search(
    strategy_factory: Callable[[dict[str, Any]], Any],
    param_grid: dict[str, Iterable],
    feed: BarFeed,
    config: BacktestConfig | None = None,
    score_key: str = "sharpe",
) -> pd.DataFrame:
    """Exhaustive grid over ``param_grid``.

    ``strategy_factory(params) -> Strategy`` must return a fresh strategy instance.
    """
    keys = list(param_grid.keys())
    rows: list[dict[str, Any]] = []
    for values in itertools.product(*(param_grid[k] for k in keys)):
        params = dict(zip(keys, values))
        strategy = strategy_factory(params)
        metrics = _run_once(strategy, feed, config)
        rows.append({**params, **metrics})
    df = pd.DataFrame(rows)
    if not df.empty and score_key in df.columns:
        df = df.sort_values(score_key, ascending=False, na_position="last")
    return df.reset_index(drop=True)


@dataclass
class WalkForwardWindow:
    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    train_metrics: dict[str, float] = field(default_factory=dict)
    test_metrics: dict[str, float] = field(default_factory=dict)
    best_params: dict[str, Any] = field(default_factory=dict)


def walk_forward(
    strategy_factory: Callable[[dict[str, Any]], Any],
    feed: BarFeed,
    param_grid: dict[str, Iterable] | None = None,
    train_bars: int = 252,
    test_bars: int = 63,
    step_bars: int | None = None,
    config: BacktestConfig | None = None,
    score_key: str = "sharpe",
    fixed_params: dict[str, Any] | None = None,
) -> list[WalkForwardWindow]:
    """Rolling train/test backtests along the feed timeline."""
    step = step_bars if step_bars is not None else test_bars
    timeline = feed.timeline
    if len(timeline) < train_bars + test_bars:
        return []

    windows: list[WalkForwardWindow] = []
    i = 0
    while i + train_bars + test_bars <= len(timeline):
        train_slice = timeline[i : i + train_bars]
        test_slice = timeline[i + train_bars : i + train_bars + test_bars]
        train_feed = slice_feed(feed, train_slice[0], train_slice[-1])
        test_feed = slice_feed(feed, test_slice[0], test_slice[-1])

        best_params = dict(fixed_params or {})
        train_metrics: dict[str, float] = {}

        if param_grid:
            grid_df = grid_search(
                strategy_factory, param_grid, train_feed, config, score_key
            )
            if grid_df.empty:
                i += step
                continue
            best_row = grid_df.iloc[0]
            best_params = {k: best_row[k] for k in param_grid}
            train_metrics = {
                k: float(best_row[k])
                for k in _METRIC_KEYS
                if k in best_row.index and pd.notna(best_row[k])
            }
        else:
            train_metrics = _run_once(strategy_factory(best_params), train_feed, config)
            train_metrics = {
                k: float(train_metrics[k])
                for k in _METRIC_KEYS
                if k in train_metrics and train_metrics[k] is not None
            }

        test_raw = _run_once(strategy_factory(best_params), test_feed, config)
        test_metrics = {
            k: float(test_raw[k])
            for k in _METRIC_KEYS
            if k in test_raw and test_raw[k] is not None
        }

        windows.append(
            WalkForwardWindow(
                train_start=train_slice[0],
                train_end=train_slice[-1],
                test_start=test_slice[0],
                test_end=test_slice[-1],
                train_metrics=train_metrics,
                test_metrics=test_metrics,
                best_params=best_params,
            )
        )
        i += step
    return windows


def walk_forward_summary(windows: list[WalkForwardWindow]) -> pd.DataFrame:
    rows = []
    for w in windows:
        row = {
            "train_start": w.train_start,
            "train_end": w.train_end,
            "test_start": w.test_start,
            "test_end": w.test_end,
            **{f"train_{k}": v for k, v in w.train_metrics.items()},
            **{f"test_{k}": v for k, v in w.test_metrics.items()},
            **{f"param_{k}": v for k, v in w.best_params.items()},
        }
        rows.append(row)
    return pd.DataFrame(rows)
