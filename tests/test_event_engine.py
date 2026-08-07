"""Core event engine smoke tests."""
from __future__ import annotations

from datetime import datetime

from quant_trading_system.core import (
    Bar,
    Direction,
    EventEngine,
    EventType,
    MarketEvent,
    SignalEvent,
)


def test_register_and_dispatch_market():
    ee = EventEngine()
    seen = []

    def handler(event):
        seen.append(event)

    ee.register(EventType.MARKET, handler)
    bar = Bar(
        symbol="TEST",
        datetime=datetime(2024, 1, 2),
        open=10.0,
        high=11.0,
        low=9.5,
        close=10.5,
        volume=1000,
    )
    ee.put(MarketEvent(bar=bar, timestamp=bar.datetime))
    n = ee.run_once()
    assert n == 1
    assert len(seen) == 1
    assert seen[0].bar.symbol == "TEST"


def test_put_left_priority():
    ee = EventEngine()
    order = []

    def on_market(e):
        order.append("M")

    def on_signal(e):
        order.append("S")

    ee.register(EventType.MARKET, on_market)
    ee.register(EventType.SIGNAL, on_signal)
    ee.put(MarketEvent())
    ee.put_left(SignalEvent(symbol="X", direction=Direction.LONG))
    ee.run_once()
    assert order == ["S", "M"]


def test_handler_exception_does_not_stop_loop():
    ee = EventEngine()
    seen = []

    def bad(e):
        raise RuntimeError("boom")

    def good(e):
        seen.append(1)

    ee.register(EventType.MARKET, bad)
    ee.register(EventType.MARKET, good)
    ee.put(MarketEvent())
    ee.run_once()
    assert seen == [1]
