from datetime import datetime
from quant_trading_system.core import Direction, SignalEvent
from quant_trading_system.portfolio import Portfolio
from quant_trading_system.risk import RiskManager

def test_order_frequency_blocks():
    rm = RiskManager(lot_size=1, max_orders_per_day=2, enforce_t1=False, max_position_pct=1.0)
    pf = Portfolio(1_000_000, t1_enabled=False)
    pos = pf._get_or_create("A")
    pos.last_price = 10.0
    sig = SignalEvent(symbol="A", direction=Direction.LONG, timestamp=datetime(2024, 1, 3))
    assert rm.check(sig, 100, pf, 10.0).approved
    assert rm.check(sig, 100, pf, 10.0).approved
    d = rm.check(sig, 100, pf, 10.0)
    assert d.approved is False
    assert "frequency" in d.reason
