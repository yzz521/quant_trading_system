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
from .vibe_bridge import (
    build_payload,
    save_latest_scan,
    submit_llm_analysis,
    submit_secondary_analysis,
)
from .vibe_format import build_display_summary
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

        # 落盘最新扫描命中（供 Vibe 页面/CLI 复用；只保留一份）
        try:
            vibe_n = int(((self.cfg or {}).get("vibe") or {}).get("candidate_count") or 15)
            save_latest_scan(Path(__file__).resolve().parents[1], scan_hits or [], market, limit=vibe_n)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 扫描结果落盘失败: %s", market, e)

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

        # 二次分析（Vibe 优先，失败时用 AI LLM 兜底；均不阻断邮件）
        vibe_summary = None
        vibe_cfg = (self.cfg or {}).get("vibe") or {}
        ai_cfg = (self.cfg or {}).get("ai") or {}
        vibe_enabled = bool(vibe_cfg.get("enabled"))
        fallback_enabled = bool(vibe_cfg.get("fallback_llm", True)) and bool(
            ai_cfg.get("enabled") and str(ai_cfg.get("api_key") or "").strip()
        )
        if (vibe_enabled or fallback_enabled) and vibe_cfg.get("on_email", True):
            try:
                from pathlib import Path as _P
                root = _P(__file__).resolve().parents[1]
                vibe_n = int(vibe_cfg.get("candidate_count") or 15)
                payload = build_payload(
                    holdings=holdings or [],
                    holding_actions=holding_actions,
                    capital_snapshot=capital_snapshot,
                    candidates=(scan_hits or [])[: vibe_n],
                    market=market,
                )
                vres = None
                if vibe_enabled:
                    try:
                        vres = submit_secondary_analysis(
                            payload,
                            root=root,
                            base_url=str(vibe_cfg.get("base_url") or "http://127.0.0.1:8899"),
                            auth_key=str(vibe_cfg.get("auth_key") or ""),
                            max_wait_sec=float(vibe_cfg.get("max_wait_sec") or 360),
                            poll_sec=float(vibe_cfg.get("poll_sec") or 3),
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning("[%s] Vibe 异常: %s", market, e)
                        vres = None
                if (not vres or not vres.get("ok")) and fallback_enabled:
                    log.info("[%s] Vibe 不可用，切换到 LLM 兜底…", market)
                    vres = submit_llm_analysis(
                        payload,
                        root=root,
                        api_key=str(ai_cfg.get("api_key") or ""),
                        base_url=str(ai_cfg.get("base_url") or "https://api.deepseek.com"),
                        model=str(
                            vibe_cfg.get("fallback_model")
                            or ai_cfg.get("model")
                            or "deepseek-chat"
                        ),
                        timeout=int(
                            vibe_cfg.get("fallback_timeout") or ai_cfg.get("timeout") or 180
                        ),
                        max_tokens=int(vibe_cfg.get("fallback_max_tokens") or 3000),
                        temperature=float(ai_cfg.get("temperature") or 0.3),
                    )
                if vres and vres.get("clean_summary"):
                    vibe_summary = vres["clean_summary"]
                elif vres and vres.get("summary"):
                    disp = build_display_summary(vres.get("summary") or "")
                    vibe_summary = disp.get("clean_summary") or (vres.get("summary") or "")[:2000]
                if vres and vres.get("partial") and vibe_summary:
                    vibe_summary = "（过程稿摘要）\n" + vibe_summary
                if not vibe_summary and vres and vres.get("error"):
                    log.warning("[%s] 二次分析无正文: %s", market, vres.get("error"))
                else:
                    log.info(
                        "[%s] 二次分析 ok=%s partial=%s source=%s",
                        market,
                        vres.get("ok") if vres else None,
                        vres.get("partial") if vres else None,
                        vres.get("source") if vres else "-",
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 二次分析跳过: %s", market, e)
                vibe_summary = None


        title, text, html = build_market_message(
            market, diagnoses, scan_hits,
            scan_enabled=bool(self.scan_cfg.get("enabled", False)),
            holdings=holdings or None, holdings_summary=h_summary,
            holding_actions=holding_actions,
            capital_snapshot=capital_snapshot,
            ai_summary=ai_summary,
            vibe_summary=vibe_summary,
        )
        log.info("[%s] 推送:\n%s", market, text[:200])
        self.notifier.send(title, text, html)

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
