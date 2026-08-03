"""Grid search + walk-forward smoke tests (synthetic data, offline)."""
from __future__ import annotations

from quant_trading_system.backtest import (
    BacktestConfig,
    grid_search,
    slice_feed,
    walk_forward,
    walk_forward_summary,
)
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import MovingAverageCrossStrategy


def _feed():
    ds = SyntheticDataSource(seed=7, annual_drift=0.08, annual_vol=0.2)
    df = ds.get_history("DEMO", "2021-01-01", "2023-06-30")
    return BarFeed({"DEMO": df})


def test_slice_feed_shortens_timeline():
    feed = _feed()
    mid = feed.timeline[len(feed.timeline) // 2]
    part = slice_feed(feed, end=mid)
    assert len(part) < len(feed)
    assert part.timeline[-1] <= mid


def test_grid_search_returns_ranked_rows():
    feed = _feed()
    cfg = BacktestConfig(
        initial_capital=500_000,
        t1_enabled=False,
        enforce_limit=False,
        enforce_volume=False,
        lot_size=1,
    )

    def factory(params):
        return MovingAverageCrossStrategy(
            ["DEMO"], fast=int(params["fast"]), slow=int(params["slow"])
        )

    df = grid_search(
        factory,
        {"fast": [5, 10], "slow": [20]},
        feed,
        config=cfg,
        score_key="sharpe",
    )
    assert len(df) == 2
    assert "sharpe" in df.columns


def test_walk_forward_produces_windows():
    feed = _feed()
    cfg = BacktestConfig(
        initial_capital=500_000,
        t1_enabled=False,
        enforce_limit=False,
        enforce_volume=False,
        lot_size=1,
    )

    def factory(params):
        return MovingAverageCrossStrategy(
            ["DEMO"],
            fast=int(params.get("fast", 5)),
            slow=int(params.get("slow", 20)),
        )

    windows = walk_forward(
        factory,
        feed,
        fixed_params={"fast": 5, "slow": 20},
        train_bars=80,
        test_bars=30,
        step_bars=30,
        config=cfg,
    )
    assert len(windows) >= 1
    summary = walk_forward_summary(windows)
    assert len(summary) >= 1
