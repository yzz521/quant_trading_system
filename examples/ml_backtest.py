"""机器学习策略回测示例 (随机森林分类器).

Trains a RandomForest on rolling technical features to predict next-bar
direction, then trades on the out-of-sample predictions. Demonstrates the ML
module end-to-end. Slower than the other examples (model retraining).

Run::

    python examples/ml_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system import BacktestConfig, BacktestEngine, PerformanceReport
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import MLStrategy


def main() -> None:
    symbols = ["ML_A", "ML_B"]
    frames = {}
    for i, sym in enumerate(symbols):
        ds = SyntheticDataSource(seed=300 + i, annual_drift=0.12, annual_vol=0.22)
        frames[sym] = ds.get_history(sym, "2022-01-01", "2024-12-31")

    feed = BarFeed(frames)
    cfg = BacktestConfig(initial_capital=1_000_000, lot_size=1, position_weight=0.25)
    engine = BacktestEngine(cfg)
    engine.add_strategy(MLStrategy(symbols, train_size=150, retrain_every=50))

    portfolio = engine.run(feed)

    report = PerformanceReport(portfolio)
    report.print_summary()
    out = report.to_html("results/ml_report.html", title="机器学习策略回测报告")
    print(f"\n报告已保存: {out}")
    print(f"最终权益: {portfolio.equity:,.2f}")


if __name__ == "__main__":
    main()
