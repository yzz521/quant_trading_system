"""Paper + 定时拉取（默认合成源，可换 Fallback=AkShare→本地）。

短跑演示::

    python examples/paper_poll_demo.py --seconds 5 --interval 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.data import FallbackDataSource, SyntheticDataSource
from quant_trading_system.execution import (
    LiveBarPoller,
    LiveConfig,
    LiveTradingEngine,
    PaperBroker,
)
from quant_trading_system.strategy import MovingAverageCrossStrategy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    symbols = ["DEMO_A"]
    # 真实行情示例（需网络 + akshare）:
    # from quant_trading_system.data import AkShareSource, LocalParquetSource, FallbackDataSource
    # source = FallbackDataSource([AkShareSource(), LocalParquetSource("data_cache")])
    source = FallbackDataSource([SyntheticDataSource(seed=3)])

    broker = PaperBroker(initial_cash=500_000)
    eng = LiveTradingEngine(
        broker,
        LiveConfig(
            initial_capital=500_000,
            lot_size=1,
            t1_enabled=False,
            heartbeat_sec=9999,
            trade_log_path="results/paper_poll_trades.jsonl",
            poll_interval=0.05,
        ),
    )
    eng.add_strategy(MovingAverageCrossStrategy(symbols, fast=3, slow=8))

    poller = LiveBarPoller(
        eng, source, symbols, interval_sec=args.interval, lookback_days=120
    )
    # 先灌一波历史，再短轮询
    n = poller.poll_once()
    print(f"initial bars pushed: {n}")

    import threading

    def run_engine():
        eng.run()

    th = threading.Thread(target=run_engine, daemon=True)
    th.start()
    poller.start()
    time.sleep(args.seconds)
    poller.stop()
    eng.stop()
    th.join(timeout=3)
    print(f"fills={len(eng.portfolio.fills)} equity={eng.portfolio.equity:.2f}")
    if hasattr(broker, "reconcile"):
        print("reconcile", broker.reconcile(eng.portfolio))


if __name__ == "__main__":
    main()
