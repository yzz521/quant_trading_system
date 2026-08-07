from quant_trading_system.core import EventEngine, EventType, MarketEvent
from quant_trading_system.data import SyntheticDataSource
from quant_trading_system.execution.live_bar_poller import LiveBarPoller


class _Eng:
    def __init__(self):
        self.event_engine = EventEngine(thread_safe=True)


def test_poll_once_pushes_bars():
    eng = _Eng()
    seen = []
    eng.event_engine.register(EventType.MARKET, lambda e: seen.append(e) if isinstance(e, MarketEvent) else None)
    poller = LiveBarPoller(eng, SyntheticDataSource(seed=2), ["DEMO"], interval_sec=60, lookback_days=30)
    n = poller.poll_once()
    eng.event_engine.run_once()
    assert n > 0
    assert len(seen) == n
    # second poll should not duplicate
    n2 = poller.poll_once()
    eng.event_engine.run_once()
    assert n2 == 0
