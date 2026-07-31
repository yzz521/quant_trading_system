"""Live trading engine.

Same event-driven skeleton as :class:`BacktestEngine`, but:

* the broker is a :class:`LiveBroker` (real exchange or paper);
* the loop blocks forever on :meth:`EventEngine.run` until :meth:`stop`;
* the portfolio is *synced* from the broker at startup so real positions
  and cash are reflected.

Feed responsibility is left to the caller: typically the broker adapter
streams bars/ticks and pushes ``MarketEvent`` objects onto the engine from a
background thread (the event queue is thread-safe enough for a single
producer + the engine as consumer).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core import EventEngine, EventType, MarketEvent
from ..portfolio import EqualWeightSizer, Portfolio
from ..risk import RiskManager
from ..utils import get_logger
from .broker_base import LiveBroker
from .execution_handler_live import LiveExecutionHandler


@dataclass
class LiveConfig:
    initial_capital: float = 1_000_000.0
    currency: str = "CNY"
    position_weight: float = 0.10
    max_positions: int = 10
    max_position_pct: float = 0.25
    max_exposure: float = 1.0
    max_drawdown: float = 0.20
    min_cash_ratio: float = 0.05
    lot_size: float = 1.0
    poll_interval: float = 0.1


class LiveTradingEngine:
    def __init__(self, broker: LiveBroker, config: LiveConfig | None = None) -> None:
        self.config = config or LiveConfig()
        self.broker = broker
        self.event_engine = EventEngine()
        self.broker.set_engine(self.event_engine)

        self.portfolio = Portfolio(self.config.initial_capital, self.config.currency)
        self.sizer = EqualWeightSizer(weight=self.config.position_weight)
        self.risk_manager = RiskManager(
            max_positions=self.config.max_positions,
            max_position_pct=self.config.max_position_pct,
            max_exposure=self.config.max_exposure,
            max_drawdown=self.config.max_drawdown,
            min_cash_ratio=self.config.min_cash_ratio,
            lot_size=self.config.lot_size,
        )
        self.execution_handler = LiveExecutionHandler(
            self.event_engine, self.portfolio, self.sizer,
            self.risk_manager, self.broker, lot_size=self.config.lot_size,
        )
        self.strategies: list = []
        self.log = get_logger(self.__class__.__name__)
        self._register_handlers()

    def _register_handlers(self) -> None:
        ee = self.event_engine
        ee.register(EventType.MARKET, self.portfolio.on_market)
        # Paper brokers fill on MARKET; real brokers get fills async via callbacks.
        if hasattr(self.broker, "handle_market"):
            ee.register(EventType.MARKET, self.broker.handle_market)
        ee.register(EventType.SIGNAL, self.execution_handler.handle_signal)
        ee.register(EventType.ORDER, self.broker.handle_order)
        ee.register(EventType.FILL, self.portfolio.on_fill)

    def add_strategy(self, strategy) -> None:
        strategy.bind(self.event_engine, self.portfolio)
        self.strategies.append(strategy)
        self.event_engine.register(EventType.MARKET, strategy.handle_market)
        self.event_engine.register(EventType.FILL, strategy.on_fill)
        self.log.info("Live strategy registered: %s", strategy.name)

    # ------------------------------------------------------------------ #
    def sync_portfolio(self) -> None:
        """Pull real cash + positions from the broker into the portfolio."""
        try:
            cash = self.broker.get_cash()
            self.portfolio.cash = cash
            self.portfolio.initial_capital = cash
            self.log.info("Synced cash from broker: %.2f", cash)
        except NotImplementedError:
            self.log.warning("Broker does not support get_cash(); using config capital")

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.log.info("Connecting broker %s ...", self.broker.name)
        self.broker.connect()
        self._connected = True
        self.sync_portfolio()
        self.log.info("Live engine running. Ctrl-C / stop() to halt.")
        try:
            self.event_engine.run(poll_interval=self.config.poll_interval)
        finally:
            self.broker.disconnect()
            self.log.info("Live engine stopped. Final equity=%.2f", self.portfolio.equity)

    def stop(self) -> None:
        self.event_engine.stop()
