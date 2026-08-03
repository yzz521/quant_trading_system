from quant_trading_system.execution.live_engine import LiveConfig, LiveTradingEngine
from quant_trading_system.execution.paper_broker import PaperBroker
from quant_trading_system.core import Direction, SignalEvent
from datetime import datetime

def test_readonly_drops_signal():
    br = PaperBroker(initial_cash=50_000)
    eng = LiveTradingEngine(br, LiveConfig(readonly=True, heartbeat_sec=9999, lot_size=1))
    br.connect()
    # seed price
    from quant_trading_system.portfolio import Position
    pos = eng.portfolio._get_or_create("A")
    pos.last_price = 10.0
    eng._on_signal(SignalEvent(symbol="A", direction=Direction.LONG, timestamp=datetime.now()))
    assert eng.execution_handler.orders_sent == 0
