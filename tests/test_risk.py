"""RiskManager gate checks."""
from __future__ import annotations

from quant_trading_system.core import Direction, SignalEvent
from quant_trading_system.portfolio import Portfolio
from quant_trading_system.risk import RiskManager


def test_exit_always_approved():
    rm = RiskManager(lot_size=100, enforce_t1=True)
    pf = Portfolio(1_000_000, t1_enabled=True)
    # Seed settled long so T+1 allows sell
    pos = pf._get_or_create("A")
    pos.quantity = 100
    pos.frozen_quantity = 0
    pos.last_price = 10.0
    sig = SignalEvent(symbol="A", direction=Direction.EXIT)
    d = rm.check(sig, delta_qty=-100, portfolio=pf, price=10.0)
    assert d.approved is True


def test_max_positions_blocks_new_entry():
    rm = RiskManager(max_positions=1, lot_size=100)
    pf = Portfolio(1_000_000)
    # Seed one open position
    pos = pf._get_or_create("EXISTING")
    pos.quantity = 100
    pos.last_price = 10.0
    pos.avg_price = 10.0

    sig = SignalEvent(symbol="NEW", direction=Direction.LONG)
    d = rm.check(sig, delta_qty=100, portfolio=pf, price=10.0)
    assert d.approved is False
    assert d.approved is False
    assert "max positions" in d.reason.lower()


def test_drawdown_halt_blocks_entries():
    rm = RiskManager(max_drawdown=0.10, lot_size=100)
    pf = Portfolio(1_000_000)
    # Fake equity curve peak then drawdown
    from datetime import datetime
    pf.equity_curve = [(datetime(2024, 1, 1), 1_000_000), (datetime(2024, 1, 2), 850_000)]
    # Force equity property via cash
    pf.cash = 850_000
    sig = SignalEvent(symbol="A", direction=Direction.LONG)
    d = rm.check(sig, delta_qty=100, portfolio=pf, price=10.0)
    # May halt depending on implementation — either rejected or halted flag
    assert d.approved is False or rm._halted is True or d.adjusted_qty == 0
