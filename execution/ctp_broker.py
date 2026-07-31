"""CTP broker adapter (China futures) — skeleton.

CTP is the standard trading interface for Chinese futures (SHFE/CFFEX/DCE/
CZCE/INE). To go live you need:

* A futures account with a broker that supports CTP (most do).
* ``investor_id``, ``password`` and ``broker_id``.
* The front-end addresses (trading front + market-data front).
* The ``vnpy_ctp`` (or raw ``thosttraderapi``) Python binding matched to your
  CTP API version.

This class is intentionally a **skeleton**: it raises ``NotImplementedError``
for every network call so nothing can be sent by accident. Fill in the
``# TODO`` sections against your vendor SDK and remove the guards.

⚠️ Real-money futures trading carries substantial risk. Test thoroughly on
the simulation account (simnow) before production.
"""
from __future__ import annotations

from ..core import OrderEvent
from ..utils import get_logger
from .broker_base import LiveBroker, LiveBrokerConfig


class CTPBroker(LiveBroker):
    name = "ctp"

    def __init__(self, config: LiveBrokerConfig,
                 investor_id: str = "", password: str = "",
                 broker_id: str = "", td_front: str = "", md_front: str = "") -> None:
        super().__init__(config)
        self.investor_id = investor_id
        self.password = password
        self.broker_id = broker_id
        self.td_front = td_front      # trading front address
        self.md_front = md_front      # market data front address
        self._api = None
        self.log = get_logger(self.__class__.__name__)

    def connect(self) -> None:
        # TODO: from vnpy_ctp import CtpGateway
        # self._api = CtpGateway(...)
        # self._api.connect(td_address=self.td_front, md_address=self.md_front,
        #                   userid=self.investor_id, password=self.password,
        #                   broker_id=self.broker_id)
        raise NotImplementedError(
            "Wire CTPBroker.connect() to vnpy_ctp with your simnow/production credentials."
        )

    def disconnect(self) -> None:
        if self._api is not None:
            # TODO: self._api.close()
            pass
        self._connected = False

    def place_order(self, order: OrderEvent) -> str:
        # TODO: translate OrderEvent -> CtpRequest and call self._api.send_order(...)
        # Map Direction.LONG/SHORT to CTP buy/sell, OrderType.MARKET to
        # LimitOrderType with a far-away price (CTP has no pure market order).
        raise NotImplementedError("Implement CTP order routing in place_order().")

    def cancel_order(self, broker_order_id: str) -> None:
        # TODO: self._api.cancel_order(broker_order_id)
        raise NotImplementedError

    def get_position(self, symbol: str) -> float:
        # TODO: query self._api.get_positions()
        return 0.0

    def get_cash(self) -> float:
        # TODO: query account from self._api.get_account()
        return 0.0
