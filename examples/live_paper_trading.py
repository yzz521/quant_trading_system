"""模拟盘交易示例 (Paper Trading).

Shows how to assemble the :class:`LiveTradingEngine` with a
:class:`PaperBroker`. A background thread replays synthetic bars into the
engine to simulate a real-time feed; the paper broker fills orders at the
incoming bar's close. This is the bridge between backtest and real live
trading — the only thing that changes when going live is swapping
``PaperBroker`` for ``CTPBroker`` / ``IBKRBroker`` / ``BinanceBroker`` and
pointing the feed at a real market-data stream.

Run::

    python examples/live_paper_trading.py
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.core import Bar, MarketEvent
from quant_trading_system.data import SyntheticDataSource
from quant_trading_system.execution import LiveConfig, LiveTradingEngine, PaperBroker
from quant_trading_system.strategy import MovingAverageCrossStrategy


def replay_feed(engine, frames, speed=0.05):
    """Replay historical frames bar-by-bar into the engine, simulating a
    real-time stream. ``speed`` is the delay between bars in seconds."""
    timeline = sorted(set().union(*[set(f.index) for f in frames.values()]))
    for ts in timeline:
        for sym, df in frames.items():
            if ts in df.index:
                row = df.loc[ts]
                bar = Bar.from_series(sym, ts.to_pydatetime(), row)
                engine.event_engine.put(MarketEvent(bar=bar, timestamp=bar.datetime))
        time.sleep(speed)
    # Let the engine drain, then stop.
    time.sleep(1.0)
    engine.stop()


def main() -> None:
    symbols = ["DEMO_A", "DEMO_B"]
    frames = {
        sym: SyntheticDataSource(seed=7 + i).get_history(sym, "2023-01-01", "2023-06-30")
        for i, sym in enumerate(symbols)
    }

    broker = PaperBroker(initial_cash=1_000_000.0)
    engine = LiveTradingEngine(broker, LiveConfig(initial_capital=1_000_000, lot_size=1))
    engine.add_strategy(MovingAverageCrossStrategy(symbols, fast=5, slow=20))

    # Drive the (fake) real-time feed on a background thread.
    feeder = threading.Thread(target=replay_feed, args=(engine, frames, 0.0), daemon=True)
    feeder.start()

    print("模拟盘启动，回放 6 个月合成数据...")
    engine.run()
    print(f"模拟盘结束。最终权益: {engine.portfolio.equity:,.2f}")
    print(f"成交笔数: {len(engine.portfolio.fills)}")


if __name__ == "__main__":
    main()
