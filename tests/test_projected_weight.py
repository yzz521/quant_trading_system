"""Portfolio-level projected weight cap."""
from __future__ import annotations

from quant_trading_system.core import Direction, SignalEvent
from quant_trading_system.portfolio import Portfolio
from quant_trading_system.risk import RiskManager


def test_projected_weight_blocks_oversized_add():
    pf = Portfolio(100_000, t1_enabled=False)
    pos = pf._get_or_create("A")
    pos.quantity = 2000  # MV 20k; equity = cash 100k + 20k = 120k
    pos.last_price = 10.0
    pos.avg_price = 10.0
    # 25% of 120k = 30k -> max qty 3000; delta max 1000
    rm = RiskManager(max_position_pct=0.25, lot_size=100, enforce_t1=False)
    d = rm.check(
        SignalEvent(symbol="A", direction=Direction.LONG),
        delta_qty=2000,
        portfolio=pf,
        price=10.0,
    )
    assert d.approved is True
    final_qty = pos.quantity + d.adjusted_qty
    max_qty = pf.equity * 0.25 / 10.0
    assert final_qty <= max_qty + 1e-6
    assert d.adjusted_qty <= 1000 + 1e-6
