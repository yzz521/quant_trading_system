"""Binance adapter (crypto spot) — skeleton.

Crypto is the easiest market to start algorithmic trading on: 24/7, free
API, generous rate limits. You need:

* A Binance account with API key + secret (spot trading enabled).
* IP whitelisting and withdrawal permissions **disabled** for safety.
* The ``python-binance`` package.

⚠️ Skeleton only — implement the ``# TODO`` calls. Never commit your API
secret; load it from an environment variable or a secrets manager.
"""
from __future__ import annotations

import os

from ..core import OrderEvent
from ..utils import get_logger
from .broker_base import LiveBroker, LiveBrokerConfig


class BinanceBroker(LiveBroker):
    name = "binance"

    def __init__(self, config: LiveBrokerConfig, testnet: bool = True) -> None:
        super().__init__(config)
        self.testnet = testnet
        self._client = None
        self.log = get_logger(self.__class__.__name__)

    def connect(self) -> None:
        # TODO: from binance.client import Client
        # api_key = self.config.api_key or os.environ["BINANCE_API_KEY"]
        # api_secret = self.config.api_secret or os.environ["BINANCE_API_SECRET"]
        # self._client = Client(api_key, api_secret, testnet=self.testnet)
        raise NotImplementedError(
            "Wire BinanceBroker.connect() to python-binance. Use testnet first."
        )

    def disconnect(self) -> None:
        if self._client is not None:
            # TODO: self._client.close_connection()
            pass
        self._connected = False

    def place_order(self, order: OrderEvent) -> str:
        # TODO: side = 'BUY' if order.direction==LONG else 'SELL'
        # resp = self._client.create_order(symbol=order.symbol, side=side,
        #       type='MARKET', quantity=order.quantity)
        # return resp['orderId']
        raise NotImplementedError("Implement Binance order routing in place_order().")

    def cancel_order(self, broker_order_id: str) -> None:
        # TODO: self._client.cancel_order(...)
        raise NotImplementedError

    def get_position(self, symbol: str) -> float:
        # TODO: parse self._client.get_asset_balance(...)
        return 0.0

    def get_cash(self) -> float:
        # TODO: parse USDT balance
        return 0.0
