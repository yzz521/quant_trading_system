"""Abstract interfaces for live brokers and real-time feeds.

A live broker plugs into the event engine exactly where the simulated broker
sits: it handles ``ORDER`` events by routing them to the exchange and turns
exchange fills back into ``FILL`` events on the queue. A live feed produces
``MARKET`` events from the broker's streaming API.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from ..core import Bar, OrderEvent


@dataclass
class LiveBrokerConfig:
    account: str = ""
    api_key: str = ""
    api_secret: str = ""
    paper: bool = True
    endpoint: str = ""


class LiveBroker(ABC):
    """Adapter around a vendor SDK (CTP / IBKR / Binance / ...)."""

    name = "live-base"

    def __init__(self, config: LiveBrokerConfig) -> None:
        self.config = config
        self._engine = None
        self._connected = False

    def set_engine(self, engine) -> None:
        self._engine = engine

    @property
    def connected(self) -> bool:
        return self._connected

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def place_order(self, order: OrderEvent) -> str:
        """Route an order. Returns the broker-assigned order id."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def get_position(self, symbol: str) -> float:
        """Return current net position (signed) for the symbol."""

    @abstractmethod
    def get_cash(self) -> float: ...

    # Convenience wrapper used as an ORDER event handler.
    def handle_order(self, event: OrderEvent) -> None:
        if not self._connected:
            raise RuntimeError(f"{self.name} is not connected")
        self.place_order(event)


class LiveFeed(ABC):
    """Streams MarketEvents into the engine in real time."""

    @abstractmethod
    def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
