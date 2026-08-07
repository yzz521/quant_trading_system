"""涨跌停拒绝成交 + 成交量上限。"""
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


def _bar(symbol, open_, close, volume=1_000_000, day=2):
    return Bar(
        symbol=symbol,
        datetime=datetime(2024, 1, day),
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=volume,
    )


def test_limit_up_rejects_buy():
    ee = EventEngine()
    fills: list[FillEvent] = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(
        slippage_bps=0,
        commission_rate=0,
        min_commission=0,
        lot_size=100,
        enforce_limit=True,
        limit_pct=0.10,
        enforce_volume=False,
    )
    broker.set_engine(ee)
    # seed prev close 10
    broker.handle_market(MarketEvent(bar=_bar("A", 10.0, 10.0, day=1)))
    broker.handle_order(OrderEvent(symbol="A", direction=Direction.LONG, quantity=100))
    # limit up open = 11
    broker.handle_market(MarketEvent(bar=_bar("A", 11.0, 11.0, day=2)))
    ee.run_once()
    assert fills == []
    assert broker.rejected_orders >= 1


def test_limit_down_rejects_sell():
    ee = EventEngine()
    fills: list = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(
        slippage_bps=0, commission_rate=0, min_commission=0, lot_size=100,
        enforce_limit=True, limit_pct=0.10, enforce_volume=False,
    )
    broker.set_engine(ee)
    broker.handle_market(MarketEvent(bar=_bar("A", 10.0, 10.0, day=1)))
    broker.handle_order(OrderEvent(symbol="A", direction=Direction.SHORT, quantity=100))
    broker.handle_market(MarketEvent(bar=_bar("A", 9.0, 9.0, day=2)))
    ee.run_once()
    assert fills == []
    assert broker.rejected_orders >= 1


def test_volume_cap_trims_qty():
    ee = EventEngine()
    fills: list[FillEvent] = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(
        slippage_bps=0, commission_rate=0, min_commission=0, lot_size=100,
        enforce_limit=False, enforce_volume=True, max_volume_pct=0.10,
    )
    broker.set_engine(ee)
    broker.handle_order(OrderEvent(symbol="A", direction=Direction.LONG, quantity=5000))
    # volume 10000 -> 10% = 1000
    broker.handle_market(MarketEvent(bar=_bar("A", 10.0, 10.0, volume=10_000, day=2)))
    ee.run_once()
    assert len(fills) == 1
    assert fills[0].quantity == 1000


def test_normal_fill_still_works():
    ee = EventEngine()
    fills: list[FillEvent] = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    broker = SimulatedBroker(
        slippage_bps=0, commission_rate=0, min_commission=0, lot_size=100,
        enforce_limit=True, enforce_volume=True, max_volume_pct=1.0,
    )
    broker.set_engine(ee)
    broker.handle_market(MarketEvent(bar=_bar("A", 10.0, 10.0, day=1)))
    broker.handle_order(OrderEvent(symbol="A", direction=Direction.LONG, quantity=200))
    broker.handle_market(MarketEvent(bar=_bar("A", 10.2, 10.3, day=2)))
    ee.run_once()
    assert len(fills) == 1
    assert fills[0].quantity == 200
