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

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..utils import get_logger, load_yaml
from .ai_summary import generate_market_summary
from .buy_power import annotate_list
from .diagnosis import StockDiagnoser
from .holdings import Holdings
from .holdings_action import analyze_holding_actions
from .notifier import Notifier, build_market_message
from .opportunity import OpportunityBatchScanner, OpportunityEngine
from .scanner import StockScanner
from .vibe_bridge import (
    build_payload,
    enrich_payload,
    save_latest_scan,
    submit_llm_analysis,
    submit_secondary_analysis,
)
from .vibe_format import build_display_summary

try:
    from zoneinfo import ZoneInfo
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING = timezone(timedelta(hours=8))

log = get_logger("Scheduler")


def _funnel_message(
    hits: list,
    stages: list,
    total: int,
    elapsed: float,
) -> tuple[str, str, str]:
    """收盘漏斗邮件：(title, text, html)。"""
    stat = " → ".join(
        f"{s.get('before', 0)}>{s.get('after', 0)}" for s in stages
    ) or "-"
    now = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
    title = f"GP助手 · 收盘漏斗 Top{len(hits)} {now}"
    lines = [f"全市场 {total} 只，漏斗：{stat}（{elapsed:.0f}s）", ""]
    for i, h in enumerate(hits, 1):
        ind = f" | 行业{h.get('industry')}" if h.get("industry") else ""
        lines.append(
            f"{i}. {h.get('code')} {h.get('name')} | {h.get('close')} "
            f"({h.get('change_pct')}%) | 评分{h.get('score')}{ind}"
        )
    rows = "".join(
        "<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s%%</td><td>%s</td><td>%s</td></tr>"
        % (i, h.get("code"), h.get("name"), h.get("close"),
           h.get("change_pct"), h.get("score"), h.get("industry") or "")
        for i, h in enumerate(hits, 1)
    )
    html = (
        "<p>全市场 <b>%s</b> 只，漏斗：<b>%s</b>（%.0fs）</p>"
        "<table border='1' cellpadding='4' style='border-collapse:collapse'>"
        "<tr><th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th><th>评分</th><th>行业</th></tr>"
        "%s</table>"
    ) % (total, stat, elapsed, rows)
    return title, "\n".join(lines), html


def _funnel_due(now: datetime, last_date: str, cfg: dict) -> bool:
    """收盘漏斗是否该触发：交易日 ≥ time 且当天还没跑过。"""
    if not cfg.get("enabled", True):
        return False
    if now.weekday() >= 5:
        return False
    hhmm = f"{now.hour:02d}:{now.minute:02d}"
    if hhmm < str(cfg.get("time") or "15:10"):
        return False
    return last_date != now.strftime("%Y-%m-%d")


def _weekly_due(now: datetime, last_date: str, cfg: dict) -> bool:
    """周报是否该触发：周五 ≥ time 且当天还没生成过。"""
    if not cfg.get("enabled", True):
        return False
    if now.weekday() != 4:
        return False
    hhmm = f"{now.hour:02d}:{now.minute:02d}"
    if hhmm < str(cfg.get("time") or "15:30"):
        return False
    return last_date != now.strftime("%Y-%m-%d")


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
        self.daily_funnel_cfg = sched.get("daily_funnel") or {}
        self.weekly_cfg = sched.get("weekly_report") or {}
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
                try:
                    payload = enrich_payload(payload)
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] payload 增强跳过: %s", market, e)
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


        # ---- V2 今日机会：批量交易计划（可选，失败不影响推送） ----
        trading_plans = None
        try:
            opp_cfg = self.opportunity_cfg or {}
            if opp_cfg.get("enabled", False):
                # 真实市场状态（指数失败时降级中性，不阻塞机会扫描）
                regime = None
                try:
                    from .market import fetch_market_context

                    mkt = fetch_market_context(
                        str(opp_cfg.get("index_symbol") or "sh000001")
                    )
                    regime = mkt.get("regime")
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] 市场状态获取失败，机会扫描用中性: %s", market, e)
                # 候选源：优先用扫描命中，其次股票池
                candidates = [h["code"] for h in (scan_hits or [])] or pool
                max_stocks = int(opp_cfg.get("max_stocks", 15))
                candidates = candidates[:max_stocks]
                if candidates:
                    log.info("[%s] 批量机会扫描 %d 只 ...", market, len(candidates))
                    engine = OpportunityEngine(
                        account_equity=float(opp_cfg.get("account_equity", 100_000)),
                        regime_score=regime.score if regime else None,
                        market_factor=regime.factor if regime else 1.0,
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
            market, diagnoses, scan_hits,
            scan_enabled=bool(self.scan_cfg.get("enabled", False)),
            holdings=holdings or None, holdings_summary=h_summary,
            holding_actions=holding_actions,
            capital_snapshot=capital_snapshot,
            ai_summary=ai_summary,
            vibe_summary=vibe_summary,
            trading_plans=trading_plans,
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

    # ------------------------------------------------------------------ #
    def run_funnel_once(self) -> dict:
        """跑一遍全市场漏斗，结果写 results/latest_funnel.json 并推送邮件。"""
        from .funnel import FunnelScanner

        funnel_cfg = (self.cfg or {}).get("funnel") or {}
        log.info("开始收盘漏斗（全市场四层过滤）…")
        result = FunnelScanner(funnel_cfg).run(holdings_mgr=self.holdings)
        data = dict(result)
        data["as_of"] = self._now_beijing().isoformat(timespec="seconds")
        root = Path(__file__).resolve().parents[1]
        out_dir = root / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "latest_funnel.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("漏斗结果已落盘 %s（Top %d）", path, len(data.get("hits") or []))

        top = (data.get("hits") or [])[: int(funnel_cfg.get("top_n") or 10)]
        if not top:
            log.warning("漏斗结果为空（可能行情源不可用），跳过邮件推送")
        else:
            title, text, html = _funnel_message(
                top, data.get("stages") or [], data.get("total") or 0,
                data.get("elapsed") or 0.0,
            )
            self.notifier.send(title, text, html)
        return data

    def _maybe_run_daily_funnel(self, now: datetime) -> None:
        """交易日 15:10 后跑一次收盘漏斗（同日不重复）。"""
        funnel_cfg = (self.cfg or {}).get("funnel") or {}
        df = self.daily_funnel_cfg
        if not funnel_cfg.get("enabled"):
            return
        root = Path(__file__).resolve().parents[1]
        path = root / "results" / "latest_funnel.json"
        last_date = ""
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                last_date = str(data.get("as_of") or "")[:10]
        except Exception:  # noqa: BLE001
            pass
        if not _funnel_due(now, last_date, df):
            return
        try:
            self.run_funnel_once()
        except Exception as e:  # noqa: BLE001
            log.error("收盘漏斗执行失败: %s", e)

    def _maybe_run_weekly_report(self, now: datetime) -> None:
        """周五 15:30 后生成周报并邮件附件发送（同日不重复）。"""
        wc = self.weekly_cfg
        root = Path(__file__).resolve().parents[1]
        last = root / "results" / "weekly" / "last_run"
        last_date = ""
        try:
            if last.exists():
                last_date = last.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass
        if not _weekly_due(now, last_date, wc):
            return
        try:
            from ..weekly_report import run as weekly_run

            holdings_list = [dict(r) for r in self.holdings.all()]
            top_n = int((self.cfg.get("funnel") or {}).get("top_n") or 10)
            codes = weekly_run.collect_codes(holdings_list, root=root, top_n=top_n)
            path = weekly_run.run_weekly_report(root, stocks=codes, top_n=top_n)
            title, text, html = weekly_run.report_email(path, codes)
            self.notifier.send(
                title, text, html,
                attachments=[(path.name, path.read_bytes())],
            )
            last.parent.mkdir(parents=True, exist_ok=True)
            last.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
            log.info("周报已生成并发送 %s", path)
        except Exception as e:  # noqa: BLE001
            log.error("周报生成/发送失败: %s", e)

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
                # 每日 15:10 收盘漏斗（同日不重复）
                try:
                    self._maybe_run_daily_funnel(now)
                except Exception as e:  # noqa: BLE001
                    log.error("收盘漏斗触发失败: %s", e)
                # 周五 15:30 周报（本周不重复）
                try:
                    self._maybe_run_weekly_report(now)
                except Exception as e:  # noqa: BLE001
                    log.error("周报触发失败: %s", e)
                status = {m: ("开" if self._in_session(m, now) else "休")
                          for m in self.enabled_markets}
                log.info("状态 %s", status)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("调度器已停止")
