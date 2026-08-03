"""SimulatedBroker fill policy and cost model."""
from __future__ import annotations

from datetime import datetime

from quant_trading_system.backtest.broker import SimulatedBroker
from quant_trading_system.core import (
    Bar,
    Direction,
    EventEngine,
    EventType,
    FillEvent,
    MarketEvent,
    OrderEvent,
)


def _bar(symbol="DEMO", open_=10.0, close=None, dt=None, volume=1_000_000):
    if close is None:
        close = open_
    return Bar(
        symbol=symbol,
        datetime=dt or datetime(2024, 1, 3),
        open=open_,
        high=max(open_, close) + 0.5,
        low=min(open_, close) - 0.5,
        close=close,
        volume=volume,
    )


def test_next_open_fill_price_with_slippage():
    ee = EventEngine()
    fills: list[FillEvent] = []

    def capture(e):
        if isinstance(e, FillEvent):
            fills.append(e)

    ee.register(EventType.FILL, capture)
    broker = SimulatedBroker(
        commission_rate=0.0003,
        stamp_duty=0.001,
        slippage_bps=10.0,  # 0.1%
        fill_policy="next_open",
        lot_size=100,
        min_commission=5.0,
        enforce_limit=False,
        enforce_volume=False,
    )
    broker.set_engine(ee)

    order = OrderEvent(symbol="DEMO", direction=Direction.LONG, quantity=200)
    broker.handle_order(order)
    broker.handle_market(MarketEvent(bar=_bar(open_=10.0)))
    ee.run_once()

    assert len(fills) == 1
    f = fills[0]
    # next_open + buy slippage: 10 * 1.001
    assert abs(f.fill_price - 10.01) < 1e-6
    assert f.quantity == 200
    # commission = max(200*10.01*0.0003, 5) = max(0.6006, 5) = 5
    assert f.commission == 5.0


def test_stamp_duty_only_on_sell():
    ee = EventEngine()
    fills: list[FillEvent] = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(
        commission_rate=0.0,
        stamp_duty=0.001,
        slippage_bps=0.0,
        fill_policy="next_open",
        lot_size=100,
        min_commission=0.0,
        enforce_limit=False,
        enforce_volume=False,
    )
    broker.set_engine(ee)

    # Sell (SHORT direction used for closing long / sell side)
    broker.handle_order(OrderEvent(symbol="DEMO", direction=Direction.SHORT, quantity=1000))
    broker.handle_market(MarketEvent(bar=_bar(open_=20.0)))
    ee.run_once()
    assert len(fills) == 1
    # duty = 1000 * 20 * 0.001 = 20
    assert abs(fills[0].commission - 20.0) < 1e-6

    fills.clear()
    broker.handle_order(OrderEvent(symbol="DEMO", direction=Direction.LONG, quantity=1000))
    broker.handle_market(MarketEvent(bar=_bar(open_=20.0, dt=datetime(2024, 1, 4))))
    ee.run_once()
    assert len(fills) == 1
    assert fills[0].commission == 0.0


def test_lot_size_rounding():
    ee = EventEngine()
    fills: list[FillEvent] = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(slippage_bps=0, fill_policy="next_open", lot_size=100, min_commission=0, commission_rate=0, enforce_limit=False, enforce_volume=False)
    broker.set_engine(ee)
    broker.handle_order(OrderEvent(symbol="DEMO", direction=Direction.LONG, quantity=250))
    broker.handle_market(MarketEvent(bar=_bar()))
    ee.run_once()
    assert len(fills) == 1
    assert fills[0].quantity == 200  # rounded down to lot
