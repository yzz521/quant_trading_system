"""Live trading engine with paper closed-loop helpers."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..core import EventEngine, EventType, FillEvent
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
    readonly: bool = False
    trade_log_path: str = "results/paper_trades.jsonl"
    heartbeat_sec: float = 30.0
    t1_enabled: bool = True


class LiveTradingEngine:
    def __init__(self, broker: LiveBroker, config: LiveConfig | None = None) -> None:
        self.config = config or LiveConfig()
        self.broker = broker
        self.event_engine = EventEngine(thread_safe=True)
        if hasattr(self.broker, "set_engine"):
            self.broker.set_engine(self.event_engine)

        self.portfolio = Portfolio(
            self.config.initial_capital,
            self.config.currency,
            t1_enabled=self.config.t1_enabled,
        )
        self.sizer = EqualWeightSizer(weight=self.config.position_weight)
        self.risk_manager = RiskManager(
            max_positions=self.config.max_positions,
            max_position_pct=self.config.max_position_pct,
            max_exposure=self.config.max_exposure,
            max_drawdown=self.config.max_drawdown,
            min_cash_ratio=self.config.min_cash_ratio,
            lot_size=self.config.lot_size,
            enforce_t1=self.config.t1_enabled,
        )
        self.execution_handler = LiveExecutionHandler(
            self.event_engine,
            self.portfolio,
            self.sizer,
            self.risk_manager,
            lot_size=self.config.lot_size,
            broker=self.broker,
        )
        self.strategies: list = []
        self.log = get_logger(self.__class__.__name__)
        self._heartbeat_stop = threading.Event()
        self._connected = False
        self._register_handlers()

    def _on_market_snapshot(self, event) -> None:
        if getattr(event, "bar", None) is not None:
            self.portfolio.snapshot(event.bar.datetime)

    def _on_signal(self, event) -> None:
        if self.config.readonly:
            self.log.info(
                "READONLY: drop signal %s %s",
                getattr(event, "symbol", ""),
                getattr(event, "direction", ""),
            )
            return
        self.execution_handler.handle_signal(event)

    def _on_fill_log(self, event: FillEvent) -> None:
        path = Path(self.config.trade_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "symbol": event.symbol,
            "direction": event.direction.name if hasattr(event.direction, "name") else str(event.direction),
            "quantity": event.quantity,
            "fill_price": event.fill_price,
            "commission": event.commission,
            "order_id": event.order_id,
            "fill_id": event.fill_id,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _register_handlers(self) -> None:
        ee = self.event_engine
        ee.register(EventType.MARKET, self.portfolio.on_market)
        ee.register(EventType.MARKET, self._on_market_snapshot)
        if hasattr(self.broker, "handle_market"):
            ee.register(EventType.MARKET, self.broker.handle_market)
        ee.register(EventType.SIGNAL, self._on_signal)
        if hasattr(self.broker, "handle_order"):
            ee.register(EventType.ORDER, self.broker.handle_order)
        ee.register(EventType.FILL, self.portfolio.on_fill)
        ee.register(EventType.FILL, self._on_fill_log)

    def add_strategy(self, strategy) -> None:
        strategy.bind(self.event_engine, self.portfolio)
        self.strategies.append(strategy)
        handler = getattr(strategy, "handle_market", None)
        if handler is not None:
            self.event_engine.register(EventType.MARKET, handler)
        if hasattr(strategy, "on_fill"):
            self.event_engine.register(EventType.FILL, strategy.on_fill)

    def sync_portfolio(self) -> None:
        try:
            cash = self.broker.get_cash()
            self.portfolio.cash = cash
            self.portfolio.initial_capital = cash
            self.log.info("Synced cash from broker: %.2f", cash)
        except NotImplementedError:
            self.log.warning("Broker does not support get_cash(); using config capital")
        if hasattr(self.broker, "reconcile"):
            snap = self.broker.reconcile(self.portfolio)
            self.log.info(
                "Reconcile snapshot: %s",
                {k: snap[k] for k in ("cash", "pending", "ok") if k in snap},
            )

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.config.heartbeat_sec):
            eq = self.portfolio.equity
            n_pos = sum(1 for p in self.portfolio.positions.values() if p.is_open)
            self.log.info(
                "HEARTBEAT equity=%.2f positions=%d fills=%d readonly=%s",
                eq,
                n_pos,
                len(self.portfolio.fills),
                self.config.readonly,
            )
            if hasattr(self.broker, "reconcile"):
                try:
                    self.broker.reconcile(self.portfolio)
                except Exception:
                    self.log.exception("reconcile failed")

    def run(self) -> None:
        self.log.info(
            "Connecting broker %s (readonly=%s) ...",
            self.broker.name,
            self.config.readonly,
        )
        self.broker.connect()
        self._connected = True
        self.sync_portfolio()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self.log.info("Live engine running. Ctrl-C / stop() to halt.")
        try:
            self.event_engine.run(poll_interval=self.config.poll_interval)
        finally:
            self._heartbeat_stop.set()
            if hasattr(self.broker, "flush_pending"):
                n = self.broker.flush_pending()
                if n:
                    self.log.info("Flushed %d pending paper orders at last price", n)
                    self.event_engine.run_once()
            self.broker.disconnect()
            self.log.info("Live engine stopped. Final equity=%.2f", self.portfolio.equity)

    def stop(self) -> None:
        self._heartbeat_stop.set()
        self.event_engine.stop()
