"""Core event-driven engine and data structures.

This is the backbone of the whole system. Every component communicates
through :class:`Event` objects pushed onto a single queue. The
:class:`EventEngine` pops events one at a time and dispatches them to the
registered handlers. Because the same loop drives both backtests and live
trading, a strategy written once can run in either context unchanged.

Event flow::

    DataFeed  --MarketEvent-->  Strategy
    Strategy  --SignalEvent--> ExecutionHandler/RiskManager
    RiskMgr   --OrderEvent----> Broker
    Broker    --FillEvent-----> Portfolio + Strategy
"""
from .event import (
    Event,
    EventType,
    MarketEvent,
    SignalEvent,
    OrderEvent,
    FillEvent,
    Direction,
    OrderType,
    Bar,
    Tick,
)
from .engine import EventEngine

__all__ = [
    "Event",
    "EventType",
    "MarketEvent",
    "SignalEvent",
    "OrderEvent",
    "FillEvent",
    "Direction",
    "OrderType",
    "Bar",
    "Tick",
    "EventEngine",
]
