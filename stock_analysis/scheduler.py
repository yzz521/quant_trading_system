"""Market-hours-aware scheduler.

Runs an analysis+notify cycle on each market only while that market is open:

* A-share (CN): every 60 min inside 9:30-11:30 / 13:00-15:00 (Beijing time)
* HK:            every 10 min inside 9:30-12:00 / 13:00-16:00
* US:            every 10 min inside 21:30-04:00 next day (winter) /
                            22:30-03:00 (summer)

The scheduler is a plain ``while`` loop on ``time.sleep`` — no extra deps — so
it can run anywhere Python runs (a tmux session, a launchd/ systemd unit, a
small cloud VM). A ``--test`` flag fires one cycle immediately for sanity.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..utils import get_logger, load_yaml
from .diagnosis import StockDiagnoser
from .scanner import StockScanner
from .notifier import Notifier, build_market_message
from .ai_summary import generate_market_summary
from .holdings import Holdings
from .buy_power import annotate_list
from .holdings_action import analyze_holding_actions

try:
    from zoneinfo import ZoneInfo
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING = timezone(timedelta(hours=8))

log = get_logger("Scheduler")


class MarketScheduler:
    def __init__(self, config_path: str = "config/notify.yaml") -> None:
        self.cfg = load_yaml(config_path) or {}
        self.stock_pools = self.cfg.get("stock_pools", {})
        self.scan_cfg = self.cfg.get("scan", {})
        self.funnel_cfg = self.cfg.get("funnel", {}) or {}
        sched = self.cfg.get("schedule", {})
        self.cn_interval = int(sched.get("cn_interval_min", 60)) * 60
        self.ushk_interval = int(sched.get("ushk_interval_min", 10)) * 60
        self.us_winter = bool(sched.get("us_winter", True))
        self.poll_interval = int(sched.get("poll_interval_sec", 60))
        # 启用的市场：只推送这些市场的分析，默认全部（CN/HK/US）
        raw_markets = self.cfg.get("enabled_markets") or ["CN", "HK", "US"]
        self.enabled_markets = [m.upper() for m in raw_markets
                                if str(m).upper() in ("CN", "HK", "US")]
        self.notifier = Notifier(config_path)
        holdings_path = str(Path(config_path).parent / "holdings.yaml")
        self.holdings = Holdings(holdings_path)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _now_beijing() -> datetime:
        return datetime.now(BEIJING)

    def _in_session(self, market: str, now: Optional[datetime] = None) -> bool:
        now = now or self._now_beijing()
        wd = now.weekday()  # Mon=0..Sun=6
        h = now.hour + now.minute / 60.0

        if market == "CN":
            if wd >= 5:
                return False
            return (9.5 <= h < 11.5) or (13.0 <= h < 15.0)

        if market == "HK":
            if wd >= 5:
                return False
            return (9.5 <= h < 12.0) or (13.0 <= h < 16.0)

        if market == "US":
            start = 21.5 if self.us_winter else 22.5
            end = 4.0 if self.us_winter else 3.0
            # evening segment Mon-Fri
            if wd < 5 and start <= h < 24:
                return True
            # early-morning segment Tue-Sat (continuation of prev night)
            if 0 < wd <= 5 and 0 <= h < end:
                return True
            return False
        return False

    def session_status(self) -> dict:
        now = self._now_beijing()
        return {m: self._in_session(m, now) for m in ("CN", "HK", "US")}

    # ------------------------------------------------------------------ #
    def _run_market(self, market: str) -> None:
        pool = self.stock_pools.get(market, [])
        if not pool:
            log.info("[%s] 股票池为空，跳过", market)
            return
        log.info("[%s] 开始分析 %d 只自选股 ...", market, len(pool))

        diagnoses = []
        for code in pool:
            try:
                r = StockDiagnoser().diagnose(code)
                diagnoses.append(r.to_dict())
            except Exception as e:  # noqa: BLE001
                log.warning("诊断 %s 失败: %s", code, e)

        scan_hits = None
        if self.scan_cfg.get("enabled", False):
            try:
                scanner = StockScanner()
                conds = self.scan_cfg.get("conditions", ["多头排列"])
                universe = self._scan_universe(market, scanner)
                if universe:
                    log.info("[%s] 扫描 %d 只标的 ...", market, len(universe))
                    hits = scanner.scan(universe, conds, limit=50)
                    scan_hits = [h.to_dict() for h in hits]
            except Exception as e:  # noqa: BLE001
                log.error("[%s] 扫描失败: %s", market, e)

        # 该市场持仓盈亏 + 卖出/加仓动作建议
        holdings, h_summary = [], None
        try:
            holdings, h_summary = self.holdings.compute_pnl(market)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 持仓盈亏计算失败: %s", market, e)

        holding_actions = None
        if holdings:
            try:
                # compute_pnl 行已含 code/cost/quantity/current_price
                holding_actions = analyze_holding_actions(holdings)
                log.info("[%s] 持仓动作分析 %d 只", market, len(holding_actions))
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 持仓动作分析失败: %s", market, e)

        # 资金约束标注（未设总资金则跳过）
        capital_snapshot = None
        try:
            capital_snapshot = self.holdings.capital_snapshot()
            if capital_snapshot is not None:
                _, diagnoses = annotate_list(
                    diagnoses, holdings_mgr=self.holdings,
                    price_key="price", default_market=market,
                )
                if scan_hits:
                    _, scan_hits = annotate_list(
                        scan_hits, holdings_mgr=self.holdings,
                        price_key="close", default_market=market,
                    )
                capital_snapshot = self.holdings.capital_snapshot()
                log.info(
                    "[%s] 资金 总%.0f 占用%.0f 可用%.0f",
                    market,
                    capital_snapshot["total_capital"],
                    capital_snapshot["invested_cost"],
                    capital_snapshot["available_cash"],
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 可买性标注失败: %s", market, e)
            capital_snapshot = None


        # AI 点评（可选，失败不影响推送）
        ai_summary = None
        try:
            ai_summary = generate_market_summary(
                self.cfg if hasattr(self, "cfg") else {},
                market=market,
                holdings=holdings,
                holdings_summary=h_summary,
                holding_actions=holding_actions,
                capital_snapshot=capital_snapshot,
                diagnoses=diagnoses,
                scan_hits=scan_hits,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] AI 点评跳过: %s", market, e)
            ai_summary = None

        title, text, html = build_market_message(
            market, diagnoses, scan_hits,
            scan_enabled=bool(self.scan_cfg.get("enabled", False)),
            holdings=holdings or None, holdings_summary=h_summary,
            holding_actions=holding_actions,
            capital_snapshot=capital_snapshot,
            ai_summary=ai_summary,
        )
        log.info("[%s] 推送:\n%s", market, text[:200])
        self.notifier.send(title, text, html)

    # ------------------------------------------------------------------ #
    def _run_funnel_cycle(self) -> None:
        """收盘后跑一次全市场四层漏斗并推送（仅 CN）。"""
        from .funnel import FunnelScanner

        log.info("[CN] 收盘漏斗开始 ...")
        funnel = FunnelScanner(self.funnel_cfg).run(holdings_mgr=self.holdings)
        if not funnel.get("hits"):
            log.warning("[CN] 收盘漏斗无命中，不推送")
            return
        title, text, html = build_market_message(
            "CN", [], None,
            scan_enabled=False,
            holdings=None,
            funnel=funnel,
        )
        log.info("[CN] 漏斗推送:\n%s", text[:300])
        self.notifier.send(title, text, html)

    def run_funnel_once(self) -> None:
        """立即执行一次收盘漏斗（供 --once-daily 与冒烟测试使用）。"""
        self._run_funnel_cycle()

    # ------------------------------------------------------------------ #
    def _scan_universe(self, market: str, scanner: StockScanner) -> list[str]:
        """Return the scan candidate pool for a market.

        CN: use ``cn_pool`` if configured, else the full A-share universe
            (capped by ``cn_universe_limit``).
        US / HK: use the configured ``us_pool`` / ``hk_pool`` code lists.
        """
        if market == "CN":
            pool = self.scan_cfg.get("cn_pool") or []
            if pool:
                return list(pool)
            limit = int(self.scan_cfg.get("cn_universe_limit", 0) or 0)
            return scanner.a_share_universe(limit=limit if limit else None)
        if market == "US":
            return list(self.scan_cfg.get("us_pool", []))
        if market == "HK":
            return list(self.scan_cfg.get("hk_pool", []))
        return []

    # ------------------------------------------------------------------ #
    def run_once(self, market: Optional[str] = None) -> None:
        """Fire one cycle for every open market (or a specific one)."""
        if market:
            self._run_market(market)
            return
        status = self.session_status()
        for m, open_ in status.items():
            if m not in self.enabled_markets:
                log.info("[%s] 未启用，跳过", m)
                continue
            if open_:
                self._run_market(m)
            else:
                log.info("[%s] 非交易时段，跳过", m)

    def run_forever(self) -> None:
        """Block forever, polling every ``poll_interval`` seconds."""
        log.info("调度器启动 | A股每%ds / 美股港股每%ds",
                 self.cn_interval, self.ushk_interval)
        last = {"CN": 0.0, "HK": 0.0, "US": 0.0}
        last_funnel = None
        try:
            while True:
                now_ts = time.time()
                now = self._now_beijing()
                intervals = {"CN": self.cn_interval, "HK": self.ushk_interval, "US": self.ushk_interval}
                for m, interval in intervals.items():
                    if m not in self.enabled_markets:
                        continue
                    if self._in_session(m, now) and now_ts - last[m] >= interval:
                        try:
                            self._run_market(m)
                        except Exception as e:  # noqa: BLE001
                            log.error("[%s] 执行失败: %s", m, e)
                        last[m] = now_ts
                # 收盘漏斗：交易日 15:10-16:00 窗口，一天一次
                if self.funnel_cfg.get("enabled", False) and now.weekday() < 5:
                    today = now.date()
                    hm = now.hour * 60 + now.minute
                    if last_funnel != today and 15 * 60 + 10 <= hm < 16 * 60:
                        try:
                            self._run_funnel_cycle()
                        except Exception as e:  # noqa: BLE001
                            log.error("[CN] 收盘漏斗失败: %s", e)
                        last_funnel = today
                status = {m: ("开" if self._in_session(m, now) else "休")
                          for m in self.enabled_markets}
                log.info("状态 %s", status)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("调度器已停止")
