"""Market-hours-aware scheduler（main-v3 精简版）。

每个开市时段跑一轮「每日决策」分析并推送邮件：
* 我的持仓盈亏 + 卖出/加仓参考
* 资金账户快照
* 今日机会（V2 批量交易计划，可选）

The scheduler is a plain ``while`` loop on ``time.sleep`` — no extra deps — so
it can run anywhere Python runs (a tmux session, a launchd/ systemd unit, a
small cloud VM). A ``--test`` flag fires one cycle immediately for sanity.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..utils import get_logger, load_yaml
from .holdings import Holdings
from .holdings_action import analyze_holding_actions
from .notifier import Notifier, build_market_message
from .opportunity import OpportunityBatchScanner, OpportunityEngine

try:
    from zoneinfo import ZoneInfo
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING = timezone(timedelta(hours=8))

log = get_logger("Scheduler")


class MarketScheduler:
    """按市场交易时段定时执行「每日决策」分析并推送邮件。"""

    def __init__(self, config_path: str = "config/notify.yaml") -> None:
        self.cfg = load_yaml(config_path) or {}
        self.stock_pools = self.cfg.get("stock_pools", {})
        sched = self.cfg.get("schedule", {})
        self.cn_interval = int(sched.get("cn_interval_min", 60)) * 60
        self.ushk_interval = int(sched.get("ushk_interval_min", 10)) * 60
        self.us_winter = bool(sched.get("us_winter", True))
        self.poll_interval = int(sched.get("poll_interval_sec", 60))
        # V2 今日机会批量扫描（每日邮件中的交易计划区块）
        self.opportunity_cfg = self.cfg.get("opportunity", {})
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
        """True if the given market is currently open (Beijing-local aware)."""
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
        log.info("[%s] 开始每日决策分析（%d 只候选）...", market, len(pool))

        # ---- 我的持仓盈亏 + 卖出/加仓参考 ----
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

        # ---- 资金账户快照 ----
        capital_snapshot = None
        try:
            capital_snapshot = self.holdings.capital_snapshot()
            if capital_snapshot:
                log.info(
                    "[%s] 资金 总%.0f 占用%.0f 可用%.0f",
                    market,
                    capital_snapshot["total_capital"],
                    capital_snapshot["invested_cost"],
                    capital_snapshot["available_cash"],
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 资金快照失败: %s", market, e)
            capital_snapshot = None

        # ---- 今日机会：V2 批量交易计划（可选，失败不影响推送） ----
        trading_plans = None
        try:
            opp_cfg = self.opportunity_cfg or {}
            if opp_cfg.get("enabled", False):
                # 真实市场状态（指数失败时降级中性，不阻塞机会扫描）
                regime = None
                try:
                    from .market import fetch_market_context

                    mkt = fetch_market_context(str(opp_cfg.get("index_symbol") or "sh000001"))
                    regime = mkt.get("regime")
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] 市场状态获取失败，机会扫描用中性: %s", market, e)
                # 候选源：全市场初筛（A股/港股/美股），失败回退股票池
                from .screener import screen_candidates

                max_stocks = int(opp_cfg.get("max_stocks", 15))
                try:
                    cands = screen_candidates(
                        market, top_n=max_stocks, config=self.cfg or {}
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] 全市场初筛失败，回退股票池: %s", market, e)
                    cands = []
                candidates = cands or [
                    {"code": c, "name": c} for c in pool[:max_stocks]
                ]
                if candidates:
                    log.info("[%s] 批量机会扫描 %d 只（初筛）...", market, len(candidates))
                    # Sector Rotation：CN 时构建板块强度+映射（失败自动中性 50）
                    sector_rank, sector_map = [], {}
                    if market == "CN":
                        try:
                            from .sector import fetch_sector_rank, get_stock_sectors
                            sector_rank = fetch_sector_rank("CN")
                            sector_map = get_stock_sectors()
                        except Exception as e:  # noqa: BLE001
                            log.warning("[%s] 板块轮动不可用（用中性）: %s", market, e)
                            sector_rank, sector_map = [], {}
                    engine = OpportunityEngine(
                        account_equity=float(opp_cfg.get("account_equity", 100_000)),
                        regime_score=regime.score if regime else None,
                        market_factor=regime.factor if regime else 1.0,
                        sector_map=sector_map,
                        sector_rank=sector_rank,
                    )
                    scanner = OpportunityBatchScanner(
                        engine=engine,
                        workers=int(opp_cfg.get("workers", 5)),
                        min_opportunity_score=float(opp_cfg.get("min_opportunity_score", 0.0)),
                    )
                    bt_res = scanner.scan(candidates, market=market)
                    trading_plans = bt_res.plans
                    if bt_res.failed:
                        log.warning("[%s] 机会扫描 %d 只失败: %s", market, len(bt_res.failed), [f["code"] for f in bt_res.failed])
                    log.info("[%s] 机会扫描完成: %d 个有效计划（%.1fs）", market, len(trading_plans), bt_res.elapsed)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] V2 机会扫描跳过: %s", market, e)
            trading_plans = None

        title, text, html = build_market_message(
            market,
            holdings=holdings or None, holdings_summary=h_summary,
            capital_snapshot=capital_snapshot,
            holding_actions=holding_actions,
            trading_plans=trading_plans,
        )
        log.info("[%s] 推送:\n%s", market, text[:200])
        self.notifier.send(title, text, html)

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

    # ------------------------------------------------------------------ #
    def run_forever(self) -> None:
        """Block forever, polling every ``poll_interval`` seconds."""
        log.info("调度器启动 | A股每%ds / 美股港股每%ds",
                 self.cn_interval, self.ushk_interval)
        last = {"CN": 0.0, "HK": 0.0, "US": 0.0}
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
                status = {m: ("开" if self._in_session(m, now) else "休")
                          for m in self.enabled_markets}
                log.info("状态 %s", status)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("调度器已停止")
