"""综合回测示例：合成数据 + 双均线趋势策略 + HTML 报告.

This is the canonical "does the system actually work" smoke test. It needs
**no network** (synthetic data), so it runs anywhere. Swap
``SyntheticDataSource`` for ``AkShareSource`` / ``YFinanceSource`` to use real
data.

Run::

    python examples/backtest_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system import BacktestConfig, BacktestEngine, PerformanceReport
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import MovingAverageCrossStrategy


def main() -> None:
    symbols = ["DEMO_A", "DEMO_B", "DEMO_C"]
    frames = {}
    for i, sym in enumerate(symbols):
        ds = SyntheticDataSource(seed=42 + i, annual_drift=0.10, annual_vol=0.25)
        frames[sym] = ds.get_history(sym, "2022-01-01", "2024-12-31")

    feed = BarFeed(frames)
    cfg = BacktestConfig(initial_capital=1_000_000, lot_size=1, allow_short=False)
    engine = BacktestEngine(cfg)
    engine.add_strategy(MovingAverageCrossStrategy(symbols, fast=5, slow=20))

    portfolio = engine.run(feed)

    report = PerformanceReport(portfolio)
    report.print_summary()
    out = report.to_html("results/backtest_demo.html", title="双均线趋势策略回测报告")
    print(f"\n报告已保存: {out}")
    print(f"最终权益: {portfolio.equity:,.2f}")


if __name__ == "__main__":
    main()
