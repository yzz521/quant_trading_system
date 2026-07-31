"""Interactive Brokers adapter (US equities/options/futures) — skeleton.

IBKR is the most common choice for retail algorithmic US trading. You need:

* An IBKR account (Pro or Lite).
* TWS or IB Gateway running with "Enable ActiveX and Socket Clients" on,
  and a fixed socket port (e.g. 7497 paper / 7496 live).
* The ``ibapi`` package (shipped with TWS) or the higher-level ``ib_insync``.

⚠️ This is a skeleton. Implement the ``# TODO`` calls and double-check on the
paper account before trading real money.
"""
from __future__ import annotations

from ..core import OrderEvent
from ..utils import get_logger
from .broker_base import LiveBroker, LiveBrokerConfig


class IBKRBroker(LiveBroker):
    name = "ibkr"

    def __init__(self, config: LiveBrokerConfig,
                 host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        super().__init__(config)
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None
        self.log = get_logger(self.__class__.__name__)

    def connect(self) -> None:
        # TODO: from ib_insync import IB
        # self._ib = IB()
        # self._ib.connect(self.host, self.port, clientId=self.client_id)
        raise NotImplementedError(
            "Wire IBKRBroker.connect() to ib_insync / ibapi against your TWS/Gateway."
        )

    def disconnect(self) -> None:
        if self._ib is not None:
            # TODO: self._ib.disconnect()
            pass
        self._connected = False

    def place_order(self, order: OrderEvent) -> str:
        # TODO: build ib_insync Stock + Order, call self._ib.placeOrder(...)
        # Map OrderType.MARKET -> 'MKT', Direction -> 'BUY'/'SELL'.
        raise NotImplementedError("Implement IBKR order routing in place_order().")

    def cancel_order(self, broker_order_id: str) -> None:
        # TODO: self._ib.cancelOrder(...)
        raise NotImplementedError

    def get_position(self, symbol: str) -> float:
        # TODO: parse self._ib.positions()
        return 0.0

    def get_cash(self) -> float:
        # TODO: parse self._ib.accountSummary('TotalCashValue')
        return 0.0
