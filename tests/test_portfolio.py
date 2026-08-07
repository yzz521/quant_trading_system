"""Portfolio cash / position accounting."""
from __future__ import annotations

from datetime import datetime

from quant_trading_system.core import Direction, FillEvent
from quant_trading_system.portfolio import Portfolio


def test_long_fill_reduces_cash_and_opens_position():
    pf = Portfolio(100_000)
    fill = FillEvent(
        symbol="A",
        direction=Direction.LONG,
        quantity=100,
        fill_price=10.0,
        commission=5.0,
        timestamp=datetime(2024, 1, 2),
    )
    pf.on_fill(fill)
    assert pf.positions["A"].quantity == 100
    assert abs(pf.positions["A"].avg_price - 10.0) < 1e-9
    # cash = 100000 - 100*10 - 5
    assert abs(pf.cash - (100_000 - 1000 - 5)) < 1e-6


def test_sell_fill_increases_cash():
    pf = Portfolio(100_000)
    pf.on_fill(FillEvent(
        symbol="A", direction=Direction.LONG, quantity=100,
        fill_price=10.0, commission=0.0, timestamp=datetime(2024, 1, 2),
    ))
    pf.on_fill(FillEvent(
        symbol="A", direction=Direction.SHORT, quantity=100,
        fill_price=12.0, commission=0.0, timestamp=datetime(2024, 1, 3),
    ))
    assert abs(pf.positions["A"].quantity) < 1e-9
    # bought 1000, sold 1200 -> cash 100000 - 1000 + 1200 = 100200
    assert abs(pf.cash - 100_200) < 1e-6
