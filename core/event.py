"""Event types and market data structures for the event-driven engine.

All events are immutable ``dataclass`` instances so they can be safely
passed between threads if the engine is ever extended to a threaded mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import pandas as pd


class EventType(str, Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"


class Direction(int, Enum):
    """Trade direction. ``EXIT`` flattens the position for the symbol."""

    LONG = 1
    SHORT = -1
    EXIT = 0


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STOP"


# --------------------------------------------------------------------------- #
# Market data structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar."""

    symbol: str
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    frequency: str = "1d"  # '1d', '1h', '5m', '1m' ...

    @classmethod
    def from_series(cls, symbol: str, dt: datetime, row: pd.Series, frequency: str = "1d") -> "Bar":
        return cls(
            symbol=symbol,
            datetime=dt,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
            frequency=frequency,
        )


@dataclass(frozen=True)
class Tick:
    symbol: str
    datetime: datetime
    price: float
    volume: float


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    type: EventType = EventType.MARKET
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class MarketEvent(Event):
    """A new bar/tick has arrived from the data feed."""

    bar: Optional[Bar] = None
    ticks: Optional[list[Tick]] = None

    def __post_init__(self):
        self.type = EventType.MARKET


@dataclass
class SignalEvent(Event):
    """A strategy wants to take or exit a position.

    ``strength`` is a unitless conviction score in [-1, 1]; portfolio sizing
    modules may use it to scale position size.
    """

    symbol: str = ""
    direction: Direction = Direction.LONG
    strength: float = 1.0

    def __post_init__(self):
        self.type = EventType.SIGNAL


@dataclass
class OrderEvent(Event):
    """A request to buy/sell. Created by the execution handler (post risk check)."""

    symbol: str = ""
    direction: Direction = Direction.LONG
    quantity: float = 0.0
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    order_id: str = ""

    def __post_init__(self):
        self.type = EventType.ORDER


@dataclass
class FillEvent(Event):
    """Confirmation that an order was (partially) filled by the broker."""

    symbol: str = ""
    direction: Direction = Direction.LONG
    quantity: float = 0.0
    fill_price: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    order_id: str = ""
    fill_id: str = ""

    @property
    def gross_value(self) -> float:
        return abs(self.quantity * self.fill_price)

    def __post_init__(self):
        self.type = EventType.FILL
