"""T+1 available quantity behaviour."""
from __future__ import annotations

from datetime import datetime, timedelta

from quant_trading_system.core import Direction, FillEvent
from quant_trading_system.portfolio import Portfolio
from quant_trading_system.risk import RiskManager
from quant_trading_system.core import SignalEvent


def _fill(symbol, direction, qty, price, day):
    return FillEvent(
        symbol=symbol,
        direction=direction,
        quantity=qty,
        fill_price=price,
        commission=0.0,
        timestamp=datetime(2024, 1, day),
    )


def test_buy_freezes_until_next_day():
    pf = Portfolio(100_000, t1_enabled=True)
    pf.on_fill(_fill("A", Direction.LONG, 100, 10.0, 2))
    assert pf.positions["A"].quantity == 100
    assert pf.positions["A"].frozen_quantity == 100
    assert pf.available("A") == 0

    # same day sell blocked by risk
    rm = RiskManager(lot_size=100, enforce_t1=True)
    sig = SignalEvent(symbol="A", direction=Direction.EXIT)
    d = rm.check(sig, delta_qty=-100, portfolio=pf, price=10.0)
    assert d.approved is False

    # next day unfreeze via session roll
    pf.snapshot(datetime(2024, 1, 3))
    assert pf.available("A") == 100
    d2 = rm.check(sig, delta_qty=-100, portfolio=pf, price=10.0)
    assert d2.approved is True
    assert abs(d2.adjusted_qty) == 100


def test_t1_disabled_allows_same_day_sell():
    pf = Portfolio(100_000, t1_enabled=False)
    pf.on_fill(_fill("A", Direction.LONG, 100, 10.0, 2))
    assert pf.available("A") == 100
    rm = RiskManager(lot_size=100, enforce_t1=False)
    d = rm.check(
        SignalEvent(symbol="A", direction=Direction.EXIT),
        -100,
        pf,
        10.0,
    )
    assert d.approved is True


def test_partial_available_after_unfreeze():
    pf = Portfolio(100_000, t1_enabled=True)
    pf.on_fill(_fill("A", Direction.LONG, 200, 10.0, 2))
    pf.snapshot(datetime(2024, 1, 3))
    assert pf.available("A") == 200
    # buy more on day 3
    pf.on_fill(_fill("A", Direction.LONG, 100, 11.0, 3))
    assert pf.positions["A"].quantity == 300
    assert pf.available("A") == 200
    assert pf.positions["A"].frozen_quantity == 100
