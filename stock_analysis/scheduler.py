"""Market-hours-aware scheduler（main-v3 精简版）。

每个开市时段跑一轮「每日决策」分析并推送邮件：
* 我的持仓盈亏 + 卖出/加仓参考
* 持仓量化（每个交易日一次：技术面+信息面，按已持有解读）
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

from ..utils import get_logger
from .app_config import enabled_markets, load_app_config
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
        self.config_path = config_path
        holdings_path = str(Path(config_path).parent / "holdings.yaml")
        self.holdings = Holdings(holdings_path)
        # 纸面结算比较贵（要拉未了结信号的行情），按「市场 + 当日」去重，每天只跑一次
        self._last_settle_date: Optional[tuple] = None
        self.reload()

    def reload(self) -> None:
        """Re-read notify.yaml so 配置页保存后下一轮调度即可生效。"""
        self.cfg = load_app_config(self.config_path)
        self.stock_pools = self.cfg.get("stock_pools", {})
        sched = self.cfg.get("schedule", {})
        self.cn_interval = int(sched.get("cn_interval_min", 60)) * 60
        self.ushk_interval = int(sched.get("ushk_interval_min", 10)) * 60
        self.us_winter = bool(sched.get("us_winter", True))
        self.poll_interval = int(sched.get("poll_interval_sec", 60))
        self.opportunity_cfg = self.cfg.get("opportunity", {})
        self.enabled_markets = enabled_markets(self.cfg)
        notify = self.cfg.get("notify")
        if notify != getattr(self, "_notify_snapshot", object()):
            self.notifier = Notifier(self.config_path)
            self._notify_snapshot = notify

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

    def _holdings_quant_for_market(
        self,
        market: str,
        holdings: list,
        holding_actions: Optional[list],
        *,
        force: bool,
    ) -> Optional[list]:
        """每个交易日对持仓跑一轮机会引擎+信息面；当日后续推送复用缓存。"""
        from .holdings_quant import analyze_holdings_quant, cached_items, save_market_cache, session_date

        today = session_date()
        if not force:
            hit = cached_items(market, today)
            if hit is not None:
                log.info("[%s] 持仓量化使用今日缓存 %d 只", market, len(hit))
                return hit
        zones = {}
        for a in holding_actions or []:
            code = a.get("code")
            if code and not a.get("error"):
                zones[str(code)] = a
        sector_rank, sector_map = [], {}
        regime_score = None
        if market == "CN":
            try:
                from .sector import fetch_sector_rank, get_stock_sectors

                sector_rank = fetch_sector_rank("CN")
                sector_map = get_stock_sectors()
            except Exception as e:  # noqa: BLE001
                log.debug("[%s] 持仓量化板块信息不可用: %s", market, e)
        try:
            from .market import regime_for_market

            # 市场状态只对 A 股有意义：港股/美股用中性，避免被上证指数牵着走
            idx = str((self.opportunity_cfg or {}).get("index_symbol") or "sh000001")
            regime_score, _ = regime_for_market(market, idx)
        except Exception as e:  # noqa: BLE001
            log.debug("[%s] 持仓量化市场状态不可用: %s", market, e)
        try:
            items = analyze_holdings_quant(
                holdings,
                fetch_news=True,
                zones=zones,
                regime_score=regime_score,
                sector_map=sector_map,
                sector_rank=sector_rank,
            )
            save_market_cache(market, today, items)
            log.info("[%s] 持仓量化完成 %d 只", market, len(items))
            return items
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 持仓量化失败: %s", market, e)
            return cached_items(market, today)

    # ------------------------------------------------------------------ #
    def _run_market(self, market: str, *, force_holdings_quant: bool = False) -> Optional[dict]:
        pool = self.stock_pools.get(market, []) or []
        log.info("[%s] 开始每日决策分析（回退池 %d 只）...", market, len(pool))

        # ---- 我的持仓盈亏 + 卖出/加仓参考 ----
        holdings, h_summary = [], None
        try:
            holdings, h_summary = self.holdings.compute_pnl(market)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 持仓盈亏计算失败: %s", market, e)

        if not pool and not holdings:
            log.info("[%s] 无回退股票池且无持仓，跳过", market)
            return None

        holding_actions = None
        if holdings:
            try:
                holding_actions = analyze_holding_actions(holdings)
                log.info("[%s] 持仓动作分析 %d 只", market, len(holding_actions))
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 持仓动作分析失败: %s", market, e)

        holding_quant = None
        if holdings:
            holding_quant = self._holdings_quant_for_market(
                market, holdings, holding_actions, force=force_holdings_quant,
            )
            # 持仓量化当天会缓存复用（可能是上午落盘的），而上面的持仓表刚取了
            # 新价 —— 用同一份快照刷新，避免同一封邮件里两张表对同一只票两个价格
            if holding_quant:
                from .holdings_quant import apply_live_prices

                apply_live_prices(holding_quant, {
                    str(h.get("code")): h.get("current_price")
                    for h in holdings if h.get("current_price")
                })

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
        sector_map: dict = {}          # 机会扫描里会填；组合风控也要用，先兜底
        try:
            opp_cfg = self.opportunity_cfg or {}
            if opp_cfg.get("enabled", False):
                # 真实市场状态（指数失败时降级中性，不阻塞机会扫描）。
                # 只对 A 股取上证状态；港股/美股中性，避免跨市场误用同一指数。
                from .market import regime_for_market

                regime_score, market_factor = regime_for_market(
                    market, str(opp_cfg.get("index_symbol") or "sh000001")
                )
                # Sector Rotation：CN 时构建板块强度+映射（失败自动中性 50）。
                # 必须在初筛**之前**取，因为初筛要用行业映射做分层取样（避免候选池
                # 被单一行业垄断）。
                sector_rank, sector_map = [], {}
                if market == "CN":
                    try:
                        from .sector import fetch_sector_rank, get_stock_sectors
                        sector_rank = fetch_sector_rank("CN")
                        sector_map = get_stock_sectors()
                    except Exception as e:  # noqa: BLE001
                        log.warning("[%s] 板块轮动不可用（用中性）: %s", market, e)
                        sector_rank, sector_map = [], {}
                # 候选源：全市场初筛（A股/港股/美股），失败回退股票池
                from .screener import screen_candidates

                max_stocks = int(opp_cfg.get("max_stocks", 15))
                try:
                    cands = screen_candidates(
                        market, top_n=max_stocks, config=self.cfg or {},
                        industry_map=sector_map or None,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] 全市场初筛失败，回退股票池: %s", market, e)
                    cands = []
                candidates = cands or [
                    {"code": c, "name": c} for c in pool[:max_stocks]
                ]
                if candidates:
                    log.info("[%s] 批量机会扫描 %d 只（初筛）...", market, len(candidates))
                    engine = OpportunityEngine(
                        account_equity=(
                            float(self.holdings.get_account().get("total_capital") or 0)
                            or float(opp_cfg.get("account_equity") or 0)
                            or None
                        ),
                        regime_score=regime_score,
                        market_factor=market_factor,
                        sector_map=sector_map,
                        sector_rank=sector_rank,
                        fetch_news=True,
                    )
                    scanner = OpportunityBatchScanner(
                        engine=engine,
                        workers=int(opp_cfg.get("workers", 5)),
                        min_opportunity_score=float(opp_cfg.get("min_opportunity_score", 0.0)),
                        min_stock_score=float(opp_cfg.get("min_stock_score", 0.0)),
                    )
                    bt_res = scanner.scan(candidates, market=market)
                    trading_plans = bt_res.plans
                    if bt_res.gate_note:
                        log.warning("[%s] 质量闸门口径提示: %s", market, bt_res.gate_note)
                    if bt_res.failed:
                        log.warning("[%s] 机会扫描 %d 只失败: %s", market, len(bt_res.failed), [f["code"] for f in bt_res.failed])
                    log.info("[%s] 机会扫描完成: %d 个有效计划（%.1fs）", market, len(trading_plans), bt_res.elapsed)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] V2 机会扫描跳过: %s", market, e)
            trading_plans = None

        # ---- 每日净值落盘：独立于持仓与组合风控 ----
        # 原先这段嵌在 `if holdings:` 与组合风控的 try 里，于是「清仓后不再记净值」，
        # 且 capital_snapshot 失败当天就丢一个点。净值曲线是回撤熔断的唯一输入，
        # 少一天就晚一天 armed，而缺失的交易日事后**无法补回**。
        curve = self._record_equity_daily(capital_snapshot, holdings)

        # ---- 组合级风控：集中度 / 行业暴露 / 相关性 / VaR / 回撤熔断 ----
        portfolio_risk = None
        if holdings:
            try:
                from .holdings_quant import portfolio_risk_block
                # 历史日收益面板：相关性/协方差/参数法 VaR 全靠它。原先不传，
                # 于是邮件里的「平均两两相关」「单日 VaR」永远算不出来。
                returns = None
                try:
                    from .returns_panel import returns_for_holdings, returns_or_none

                    # 不能用 `returns_for_holdings(...) or None`：DataFrame 布尔值有歧义
                    returns = returns_or_none(returns_for_holdings(holdings))
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] 历史收益面板不可用（相关性与 VaR 暂缺）: %s", market, e)
                portfolio_risk = portfolio_risk_block(
                    holdings,
                    # 与净值落盘共用同一口径（原先这里引用了从未定义的
                    # total_equity → NameError 被 except 吞掉 → 组合风控
                    # 与回撤熔断**从未真正执行过**）
                    total_equity=self._net_worth(capital_snapshot, holdings),
                    sector_map=sector_map if market == "CN" else None,
                    equity_curve=curve,
                    returns=returns,
                )
                if portfolio_risk:
                    log.info("[%s] 组合风控: %s", market, portfolio_risk.get("verdict"))
                    if portfolio_risk.get("breaches"):
                        log.warning("[%s] 组合风控超限: %s",
                                    market, "; ".join(portfolio_risk["breaches"]))
                    # 熔断系数真正生效：回撤越深，新开仓的建议仓位越小
                    brake = float(portfolio_risk.get("brake", 1.0) or 1.0)
                    if brake < 1.0 and trading_plans:
                        from .portfolio_risk import apply_brake_to_plans

                        apply_brake_to_plans(trading_plans, brake)
                        log.warning("[%s] 回撤熔断生效：新开仓建议仓位 ×%.0f%%",
                                    market, brake * 100)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 组合风控计算失败: %s", market, e)
                portfolio_risk = None

        title, text, html = build_market_message(
            market,
            holdings=holdings or None, holdings_summary=h_summary,
            capital_snapshot=capital_snapshot,
            holding_quant=holding_quant,
            holding_actions=holding_actions,
            trading_plans=trading_plans,
            portfolio_risk=portfolio_risk,
        )
        log.info("[%s] 推送:\n%s", market, text[:200])
        self.notifier.send(title, text, html)

        # ---- 纸面跟踪：记录可执行信号 + 每日结算 ----
        self._track_paper(trading_plans, market)

        # ---- 影子模式：生成订单意图但**不发送** ----
        # 决策条件 6/7 的实测场：在零资金风险下把执行层（下单 + 5 道风控闸门）
        # 完整跑一遍。与纸面跟踪一样是旁路观测，失败不影响推送与任何决策。
        self._shadow_tick(trading_plans, market, capital_snapshot, holdings)

        return {
            "holdings_n": len(holdings or []),
            "actions_n": len(holding_actions or []),
        }

    # ------------------------------------------------------------------ #
    def _track_paper(self, trading_plans, market: str) -> None:
        """纸面跟踪：把可执行信号落盘，并每天结算一次。

        这是**旁路观测**：它失败绝不能影响推送。结算复用回测的成交规则，
        所以纸面成绩单可以用来检验回测结论，而不是又一套自说自话的口径。
        """
        try:
            from .paper_tracking import record_signals, settle_signals
            from .portfolio_risk import beijing_date

            fresh = record_signals(trading_plans or [])
            if fresh:
                log.info("[%s] 纸面跟踪记录 %d 个新信号", market, len(fresh))

            today = str(beijing_date())
            if self._last_settle_date != (market, today):
                self._last_settle_date = (market, today)
                settled = settle_signals(market=market)
                if settled:
                    log.info("[%s] 纸面跟踪结算 %d 笔", market, len(settled))
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 纸面跟踪跳过: %s", market, e)

    # ------------------------------------------------------------------ #
    def _shadow_tick(self, trading_plans, market: str,
                     capital_snapshot=None, holdings=None) -> None:
        """影子模式：把交易计划转成订单意图并落盘，**不发送任何委托**。

        与 ``_track_paper`` 同样是**旁路观测**：整段吞异常，它失败绝不能影响推送。

        行情新鲜度：真实上线时这里必须换成券商行情/交易网关的时间戳；影子模式下
        用「本轮取数完成的时间」—— 它就是这份数据实际的新鲜度（本轮扫描刚刚完成）。
        """
        try:
            import time

            from .shadow_orders import (
                build_shadow_orders,
                load_shadow_orders,
                record_shadow_orders,
                recordable,
            )

            # 总资产必须与组合风控**同一口径**（市值 + 现金）。这里 equity 同时是
            # 「仓位计算基准」和「单票/总仓位闸门的分母」：若分母用成本口径的本金、
            # 分子用市值算持仓，两把尺子会在有浮盈浮亏时给出互相矛盾的结论。
            equity = self._net_worth(capital_snapshot, holdings)
            if not equity:
                log.debug("[%s] 影子模式跳过：无总资产", market)
                return

            # 已持有市值：按代码汇总（单票闸门用），同时给出合计（总仓位闸门用）。
            # 持仓行没有实时价时退回成本价 —— 宁可口径略粗，也不要因为缺一个字段就整段跳过。
            held_by_code: dict = {}
            holdings_value = 0.0
            for h in holdings or []:
                code = str(h.get("code") or "")
                if not code:
                    continue
                qty = float(h.get("quantity") or 0)
                px = float(h.get("current_price") or h.get("cost_price") or 0)
                val = qty * px
                held_by_code[code] = held_by_code.get(code, 0.0) + val
                holdings_value += val

            rows = load_shadow_orders()
            keys = [(str(r.get("date")), str(r.get("code")), str(r.get("side"))) for r in rows]

            run = build_shadow_orders(
                trading_plans or [],
                equity=equity,
                holdings_value=holdings_value,
                held_by_code=held_by_code,
                existing_keys=keys,
                market=market,
                quote_ts=time.time(),
            )
            fresh = record_shadow_orders(recordable(run))
            if fresh:
                log.info("[%s] 影子模式：新增 %d 条订单意图（未发送）", market, len(fresh))
            for note in run.guard_notes:
                log.warning("[%s] 影子模式闸门：%s", market, note)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 影子模式跳过: %s", market, e)

    # ------------------------------------------------------------------ #
    def _record(self, market: str, *, ok: bool, detail: str = "",
                holdings_n: int = 0, actions_n: int = 0) -> None:
        """把本轮结果落盘到 ``results/scheduler_state.json``。

        观测性只服务于排障，绝不能因为它自己失败而中断调度 —— 整段吞异常。
        """
        try:
            from .scheduler_state import record_run

            record_run(
                market, ok=ok, detail=detail,
                holdings_n=holdings_n, actions_n=actions_n,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("调度状态落盘失败（不影响调度）: %s", e)

    def _net_worth(self, capital_snapshot, holdings) -> Optional[float]:
        """账户净值 = 持仓市值（现价优先，退回成本价）+ 可用现金。

        **必须只有这一处口径**。组合风控的「市值 / 总资产」权重与净值落盘用的
        是同一把尺子，两处各算一遍必然分叉：一边按现价、一边按成本价，同一个
        账户在同一天会得到两个「总资产」，于是集中度超限与回撤点位互相矛盾。

        拿不到 ``total_capital`` 时返回 None（宁可缺一个点，也不要用 0 污染曲线
        和权重分母）。市值与现金都为 0 时退回 ``total_capital``。
        """
        snap = capital_snapshot or {}
        total = snap.get("total_capital")
        if not total:
            return None
        mv = sum(
            float(h.get("current_price") or h.get("cost_price") or 0)
            * float(h.get("quantity") or 0)
            for h in (holdings or [])
        )
        net_worth = mv + float(snap.get("available_cash") or 0)
        return net_worth if net_worth > 0 else float(total)

    def _record_equity_daily(self, capital_snapshot, holdings) -> Optional[list]:
        """把当日净值写进 ``equity_history.json``，返回完整曲线（失败返回 None）。

        刻意**不**放在 ``if holdings:`` 里：清仓后净值 = 现金，仍是有效观测，
        而回撤熔断正是「亏到一定程度才触发」——最需要这条曲线的时刻，恰恰可能
        没有持仓。``record_equity`` 按日去重（同日覆盖），所以一个交易日里调度器
        跑多少轮都只留一个点。
        """
        try:
            from .portfolio_risk import (
                equity_values,
                load_equity_history,
                record_equity,
            )

            net_worth = self._net_worth(capital_snapshot, holdings)
            if net_worth is None:
                # 拿不到总资金就不写当日点，但把已有曲线交出去（回撤仍可算）
                return equity_values(load_equity_history()) or None
            record_equity(net_worth)
            return equity_values(load_equity_history()) or None
        except Exception as e:  # noqa: BLE001
            log.warning("净值记录失败（当日曲线点缺失，回撤熔断将少一天依据）: %s", e)
            return None

    def _run_and_record(self, market: str, **kw) -> None:
        """跑一轮并记录结果。异常照旧向上抛（``--test`` 需要看到真实错误）。"""
        info = self._run_market(market, **kw) or {}
        self._record(
            market, ok=True,
            detail=f"holdings={info.get('holdings_n', 0)} actions={info.get('actions_n', 0)}",
            holdings_n=info.get("holdings_n", 0),
            actions_n=info.get("actions_n", 0),
        )

    # ------------------------------------------------------------------ #
    def run_once(self, market: Optional[str] = None) -> None:
        """Fire one cycle for every open market (or a specific one)."""
        self.reload()
        if market:
            self._run_and_record(market, force_holdings_quant=True)
            return
        status = self.session_status()
        for m, open_ in status.items():
            if m not in self.enabled_markets:
                log.info("[%s] 未启用，跳过", m)
                continue
            if open_:
                self._run_and_record(m, force_holdings_quant=True)
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
                self.reload()
                now_ts = time.time()
                now = self._now_beijing()
                intervals = {"CN": self.cn_interval, "HK": self.ushk_interval, "US": self.ushk_interval}
                for m, interval in intervals.items():
                    if m not in self.enabled_markets:
                        continue
                    if self._in_session(m, now) and now_ts - last[m] >= interval:
                        try:
                            info = self._run_market(m) or {}
                            self._record(
                                m, ok=True,
                                detail=f"holdings={info.get('holdings_n', 0)} "
                                       f"actions={info.get('actions_n', 0)}",
                                holdings_n=info.get("holdings_n", 0),
                                actions_n=info.get("actions_n", 0),
                            )
                        except Exception as e:  # noqa: BLE001
                            log.error("[%s] 执行失败: %s", m, e)
                            self._record(m, ok=False, detail=f"执行失败: {e}")
                        last[m] = now_ts
                status = {m: ("开" if self._in_session(m, now) else "休")
                          for m in self.enabled_markets}
                log.info("状态 %s", status)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("调度器已停止")
