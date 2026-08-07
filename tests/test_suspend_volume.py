from datetime import datetime
from quant_trading_system.backtest.broker import SimulatedBroker
from quant_trading_system.core import Bar, Direction, EventEngine, EventType, FillEvent, MarketEvent, OrderEvent

def test_zero_volume_defers_then_fills():
    ee = EventEngine()
    fills = []
    ee.register(EventType.FILL, lambda e: fills.append(e) if isinstance(e, FillEvent) else None)
    br = SimulatedBroker(commission_rate=0, min_commission=0, slippage_bps=0, lot_size=100,
                         enforce_limit=False, enforce_volume=False, skip_zero_volume=True)
    br.set_engine(ee)
    br.handle_order(OrderEvent(symbol="A", direction=Direction.LONG, quantity=100))
    br.handle_market(MarketEvent(bar=Bar("A", datetime(2024,1,2), 10,10,10,10, volume=0)))
    ee.run_once()
    assert fills == []
    assert len(br._pending) == 1
    br.handle_market(MarketEvent(bar=Bar("A", datetime(2024,1,3), 10,10,10,10, volume=1e6)))
    ee.run_once()
    assert len(fills) == 1
