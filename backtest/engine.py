"""The backtest engine — wires every component into the event loop.

Typical usage::

    cfg = BacktestConfig(initial_capital=1_000_000)
    engine = BacktestEngine(cfg)
    engine.add_strategy(MovingAverageCrossStrategy(["600000"], fast=5, slow=20))
    engine.run(feed)
    portfolio = engine.results()
    PerformanceReport(portfolio).save("results/report.html")

The engine is just an assembler: it builds an :class:`EventEngine`, registers
handlers in the correct order (portfolio -> broker -> strategies on MARKET;
portfolio -> strategies on FILL), then drives the feed. Swap the feed and
broker for live versions and the rest runs unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core import EventEngine, EventType, MarketEvent
from ..portfolio import EqualWeightSizer, Portfolio
from ..risk import RiskManager
from ..utils import get_logger
from .broker import SimulatedBroker
from .execution_handler import ExecutionHandler


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    currency: str = "CNY"
    # Broker costs
    commission_rate: float = 0.0003
    stamp_duty: float = 0.001
    slippage_bps: float = 2.0
    fill_policy: str = "next_open"
    allow_short: bool = False
    lot_size: float = 1.0
    t1_enabled: bool = True  # A-share style T+1 settlement
    min_commission: float = 5.0
    # Broker realism
    limit_pct: float = 0.10
    enforce_limit: bool = True
    max_volume_pct: float = 0.25
    enforce_volume: bool = True
    # Sizing
    position_weight: float = 0.10
    # Risk
    max_positions: int = 10
    max_position_pct: float = 0.25
    max_exposure: float = 1.0
    max_drawdown: float = 0.20
    min_cash_ratio: float = 0.05


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        cfg = self.config

        self.event_engine = EventEngine()
        self.portfolio = Portfolio(cfg.initial_capital, cfg.currency, t1_enabled=cfg.t1_enabled)
        self.broker = SimulatedBroker(
            commission_rate=cfg.commission_rate,
            stamp_duty=cfg.stamp_duty,
            slippage_bps=cfg.slippage_bps,
            fill_policy=cfg.fill_policy,
            allow_short=cfg.allow_short,
            lot_size=cfg.lot_size,
            min_commission=cfg.min_commission,
            limit_pct=cfg.limit_pct,
            enforce_limit=cfg.enforce_limit,
            max_volume_pct=cfg.max_volume_pct,
            enforce_volume=cfg.enforce_volume,
        )
        self.broker.set_engine(self.event_engine)
        self.sizer = EqualWeightSizer(weight=cfg.position_weight)
        self.risk_manager = RiskManager(
            max_positions=cfg.max_positions,
            max_position_pct=cfg.max_position_pct,
            max_exposure=cfg.max_exposure,
            max_drawdown=cfg.max_drawdown,
            min_cash_ratio=cfg.min_cash_ratio,
            lot_size=cfg.lot_size,
            enforce_t1=cfg.t1_enabled,
        )
        self.execution_handler = ExecutionHandler(
            self.event_engine, self.portfolio, self.sizer,
            self.risk_manager, lot_size=cfg.lot_size,
        )
        self.strategies: list = []
        self.feed = None
        self.log = get_logger(self.__class__.__name__)
        self._register_core_handlers()

    # ------------------------------------------------------------------ #
    def _register_core_handlers(self) -> None:
        ee = self.event_engine
        # MARKET: update prices -> fill pending orders -> strategies react
        ee.register(EventType.MARKET, self.portfolio.on_market)
        ee.register(EventType.MARKET, self.broker.handle_market)
        # SIGNAL -> risk-checked order
        ee.register(EventType.SIGNAL, self.execution_handler.handle_signal)
        # ORDER -> broker queue
        ee.register(EventType.ORDER, self.broker.handle_order)
        # FILL: portfolio updates before strategies are notified
        ee.register(EventType.FILL, self.portfolio.on_fill)

    # ------------------------------------------------------------------ #
    def add_strategy(self, strategy) -> None:
        strategy.bind(self.event_engine, self.portfolio)
        self.strategies.append(strategy)
        self.event_engine.register(EventType.MARKET, strategy.handle_market)
        self.event_engine.register(EventType.FILL, strategy.on_fill)
        self.log.info("Registered strategy: %s (universe=%d)", strategy.name, len(strategy.symbols))

    def use_sizer(self, sizer) -> None:
        """Swap the position sizer (e.g. for VolTargetSizer)."""
        self.sizer = sizer
        self.execution_handler.sizer = sizer

    # ------------------------------------------------------------------ #
    def run(self, feed) -> Portfolio:
        self.feed = feed
        self.log.info(
            "Backtest starting | capital=%s %s | bars=%d | strategies=%d",
            self.config.initial_capital, self.config.currency,
            len(feed), len(self.strategies),
        )
        bar_count = 0
        for ts, bars in feed:
            for bar in bars:
                self.event_engine.put(MarketEvent(bar=bar, timestamp=bar.datetime))
            self.event_engine.run_once()
            self.portfolio.snapshot(ts)
            bar_count += 1
        self.log.info(
            "Backtest done | bars=%d | events=%d | orders=%d | final equity=%.2f",
            bar_count, self.event_engine.events_processed,
            self.execution_handler.orders_sent, self.portfolio.equity,
        )
        return self.portfolio

    def results(self) -> Portfolio:
        return self.portfolio
