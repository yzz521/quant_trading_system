"""Offline demo: grid search on synthetic data."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quant_trading_system.backtest import BacktestConfig, grid_search, walk_forward, walk_forward_summary
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import MovingAverageCrossStrategy

def main():
    ds = SyntheticDataSource(seed=42)
    feed = BarFeed({"DEMO": ds.get_history("DEMO", "2021-01-01", "2024-06-30")})
    cfg = BacktestConfig(t1_enabled=False, enforce_limit=False, enforce_volume=False, lot_size=1)
    def factory(p):
        return MovingAverageCrossStrategy(["DEMO"], fast=int(p["fast"]), slow=int(p["slow"]))
    print(grid_search(factory, {"fast": [5, 10], "slow": [20, 40]}, feed, config=cfg))
    w = walk_forward(factory, feed, fixed_params={"fast": 5, "slow": 20}, train_bars=120, test_bars=40, config=cfg)
    print(walk_forward_summary(w))

if __name__ == "__main__":
    main()
