"""多因子选股回测示例.

Generates a 10-name synthetic universe, runs the cross-sectional multi-factor
strategy and reports performance. Demonstrates how the engine handles a
multi-symbol, periodically-rebalanced strategy.

Run::

    python examples/multi_factor_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system import BacktestConfig, BacktestEngine, PerformanceReport
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import MultiFactorStrategy


def main() -> None:
    n_names = 10
    symbols = [f"STK{i:02d}" for i in range(n_names)]
    frames = {}
    for i, sym in enumerate(symbols):
        # Mix of drift/vol so the cross section has real dispersion.
        ds = SyntheticDataSource(
            seed=100 + i,
            annual_drift=0.05 + 0.01 * i,
            annual_vol=0.15 + 0.01 * (i % 5),
        )
        frames[sym] = ds.get_history(sym, "2022-01-01", "2024-12-31")

    feed = BarFeed(frames)
    cfg = BacktestConfig(initial_capital=2_000_000, lot_size=1,
                         position_weight=0.20, max_positions=5)
    engine = BacktestEngine(cfg)
    engine.add_strategy(MultiFactorStrategy(symbols, rebalance_days=5, top_n=3))

    portfolio = engine.run(feed)

    report = PerformanceReport(portfolio)
    report.print_summary()
    out = report.to_html("results/multi_factor_report.html",
                         title="多因子选股回测报告")
    print(f"\n报告已保存: {out}")
    print(f"最终权益: {portfolio.equity:,.2f}")


if __name__ == "__main__":
    main()
