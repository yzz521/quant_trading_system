"""Paper 全链路闭环 demo（离线合成行情）。

链路: 行情线程 → MARKET → 策略 → SIGNAL → 风控 → ORDER → PaperBroker → FILL → 日志

Run::

    python examples/paper_loop_demo.py
    # 只读观察:
    python examples/paper_loop_demo.py --readonly
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.analytics import compute_metrics, compute_benchmark_metrics, synthetic_benchmark_from_drift
from quant_trading_system.core import Bar, MarketEvent
from quant_trading_system.data import SyntheticDataSource
from quant_trading_system.execution import LiveConfig, LiveTradingEngine, PaperBroker
from quant_trading_system.strategy import MovingAverageCrossStrategy


def replay_feed(engine, frames, speed=0.0):
    timeline = sorted(set().union(*[set(f.index) for f in frames.values()]))
    for ts in timeline:
        for sym, df in frames.items():
            if ts in df.index:
                bar = Bar.from_series(sym, ts.to_pydatetime(), df.loc[ts])
                engine.event_engine.put(MarketEvent(bar=bar, timestamp=bar.datetime))
        if speed:
            time.sleep(speed)
    time.sleep(1.0)
    engine.event_engine.run_once()
    engine.stop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readonly", action="store_true")
    ap.add_argument("--log", default="results/paper_trades.jsonl")
    args = ap.parse_args()

    symbols = ["DEMO_A"]
    frames = {
        sym: SyntheticDataSource(seed=7).get_history(sym, "2023-01-01", "2023-06-30")
        for sym in symbols
    }

    Path("results").mkdir(exist_ok=True)
    broker = PaperBroker(initial_cash=1_000_000.0, commission_rate=0.0003, slippage_bps=2.0)
    cfg = LiveConfig(
        initial_capital=1_000_000.0,
        lot_size=1,
        readonly=args.readonly,
        trade_log_path=args.log,
        heartbeat_sec=9999,  # demo 很短，避免刷屏
        t1_enabled=False,
        poll_interval=0.01,
    )
    engine = LiveTradingEngine(broker, cfg)
    engine.add_strategy(MovingAverageCrossStrategy(symbols, fast=5, slow=20))

    feeder = threading.Thread(target=replay_feed, args=(engine, frames, 0.0), daemon=True)
    feeder.start()
    print(f"Paper 闭环启动 readonly={args.readonly} log={args.log}")
    engine.run()
    feeder.join(timeout=5)

    pf = engine.portfolio
    m = compute_metrics(pf)
    print("--- 绩效 ---")
    for k in ("total_return", "sharpe", "max_drawdown", "n_orders", "final_equity"):
        if k in m:
            print(f"  {k}: {m[k]}")
    # 基准对比（合成指数）
    eq = pf.equity_curve_frame()
    if not eq.empty:
        bm = synthetic_benchmark_from_drift(eq.index, seed=1)
        bm_m = compute_benchmark_metrics(pf, bm)
        print("--- 相对合成基准 ---")
        for k in ("excess_total_return", "information_ratio", "beta", "correlation"):
            if k in bm_m:
                print(f"  {k}: {bm_m[k]:.4f}")
    if Path(args.log).exists():
        n = sum(1 for _ in open(args.log, encoding="utf-8"))
        print(f"成交日志行数: {n} → {args.log}")
    if hasattr(broker, "reconcile"):
        print("对账:", broker.reconcile(pf))


if __name__ == "__main__":
    main()
