"""Thread-safe EventEngine + PaperBroker order tracking."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from quant_trading_system.core import (
    Bar,
    Direction,
    EventEngine,
    EventType,
    MarketEvent,
    OrderEvent,
)
from quant_trading_system.execution.paper_broker import OrderStatus, PaperBroker


def test_thread_safe_put_from_worker():
    ee = EventEngine(thread_safe=True)
    seen = []

    def on_market(e):
        if isinstance(e, MarketEvent) and e.bar:
            seen.append(e.bar.symbol)

    ee.register(EventType.MARKET, on_market)

    def producer():
        for i in range(20):
            bar = Bar(
                symbol=f"S{i%3}",
                datetime=datetime(2024, 1, 1),
                open=10, high=10, low=10, close=10, volume=1000,
            )
            ee.put(MarketEvent(bar=bar))
            time.sleep(0.001)

    t = threading.Thread(target=producer)
    t.start()
    # drain for a short while
    deadline = time.time() + 1.0
    while time.time() < deadline and len(seen) < 20:
        ee.run_once()
        time.sleep(0.005)
    t.join(timeout=2)
    ee.run_once()
    assert len(seen) == 20


def test_paper_idempotent_order():
    pb = PaperBroker(initial_cash=100_000)
    pb.connect()
    o = OrderEvent(symbol="A", direction=Direction.LONG, quantity=100, order_id="oid1")
    assert pb.place_order(o) == "oid1"
    assert pb.place_order(o) == "oid1"  # duplicate
    assert len(pb.pending_orders()) == 1


def test_paper_fill_and_reconcile():
    ee = EventEngine(thread_safe=True)
    pb = PaperBroker(initial_cash=100_000, commission_rate=0, slippage_bps=0)
    pb.set_engine(ee)
    pb.connect()
    pb.place_order(OrderEvent(symbol="A", direction=Direction.LONG, quantity=100, order_id="x1"))
    bar = Bar(
        symbol="A", datetime=datetime(2024, 1, 2),
        open=10, high=10, low=10, close=10, volume=1e6,
    )
    pb.handle_market(MarketEvent(bar=bar))
    assert pb.get_position("A") == 100
    assert pb._orders["x1"].status == OrderStatus.FILLED
    snap = pb.reconcile()
    assert snap["positions"]["A"] == 100
    assert abs(snap["cash"] - (100_000 - 1000)) < 1e-6
