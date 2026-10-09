"""Trading Plan 历史回测引擎。

验证「入场区间 / 止损 / 目标价」规则在历史上是否有效（计划书 §17）。

严格防 look-ahead bias：
  * 生成计划：只用截至 T 日的K线（``df.iloc[:i+1]``）→ 引擎内部只看到历史
  * 评估结果：只用 T 日之后的数据（入场触发、止损/目标命中、收益）
  * 指标（MA/ATR/BOLL 等）均为因果计算，不引入未来值

成交真实性（2026-09 起，见 ``backtest/execution.py``）
--------------------------------------------------
原实现只在 ``low <= entry_high`` 时判定成交，成交价却取现价**之下**的支撑位，
等于以**买不到的幽灵价**建仓。现在：

  * 限价买入必须**真的触及委托价**：``open <= entry_price``（开盘成交）或
    ``low <= entry_price <= high``（限价成交）；两者都不满足 → 当日不成交。
  * 逐笔扣除佣金 / 印花税 / 过户费 / 滑点（按市场预设）。
  * 止损按跳空口径成交：``min(开盘价, 止损价)``；目标按 ``max(开盘价, 目标价)``。
  * 一字涨停买不进、一字跌停卖不出、停牌日不成交（顺延到下一可交易日）。
  * 容量约束：单日最多吃掉当日成交额的固定比例，超出部分按比例缩量。

重叠信号
--------
默认 ``overlap_policy="independent"`` 保留逐笔统计（与历史报告可比），但会标记
``overlaps_prior`` 并在指标里报告 ``overlap_rate``；``overlap_policy="sequential"``
则在前一笔持仓未平仓前不再开新仓。无论哪种，``metrics.portfolio_*`` 都是按现金与
持仓结算的**可实现**口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..opportunity.opportunity_engine import OpportunityEngine
from ..opportunity.quality_gate import QualityGateConfig
from ..opportunity.trading_plan import DecisionState
from .execution import (
    LIMIT_PCT_DEFAULT,
    ExecutionConfig,
    apply_market,
    is_suspended,
    limit_pct_for_code,
    price_limit_state,
)
from .metrics import BacktestMetrics, calc_metrics, simulate_portfolio


@dataclass
class BacktestTrade:
    """单笔模拟交易记录（含计划时点信息，便于审计）。"""

    date: str = ""
    decision: str = ""
    entry_low: float = 0.0
    entry_price: float = 0.0
    entry_high: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0

    # 计划生成时点的打分快照——置信度标定需要 (置信度 → 实际结果) 配对样本，
    # 缺了这些字段就无法拟合，也无法做「打分是否真有分辨力」的归因。
    confidence: Optional[float] = None
    opportunity_score: Optional[float] = None
    stock_score: Optional[float] = None
    risk_reward_1: Optional[float] = None

    entry_executed: bool = False      # 后续价格是否**真的**触及委托价并成交
    entry_exec_price: float = 0.0
    exit_reason: str = ""             # stop_loss / target_2 / timeout / not_entered / overlap_skipped
    return_pct: float = 0.0           # 扣费后净收益（%）
    holding_days: int = 0
    hit_target_1: bool = False
    hit_target_2: bool = False

    # ---- 成交真实性字段 ----
    gross_return_pct: Optional[float] = None   # 扣费前收益（%）
    cost_pct: float = 0.0                      # 本笔成本侵蚀（百分点）
    min_commission_pct: float = 0.0            # 其中「最低佣金补差」贡献（百分点）
    fill_type: str = ""                        # open / limit / ""（未成交）
    days_to_entry: int = 0
    gapped: bool = False                       # 止损/目标是否因跳空以更差价成交
    limit_blocked_days: int = 0                # 因涨停封板/停牌而无法入场的天数
    capacity_fill_ratio: float = 1.0           # 容量约束下的可成交比例
    capacity_limited: bool = False
    capital_weight: float = 1.0                # 资金权重（= capacity_fill_ratio）
    planned_position_amount: float = 0.0       # 计划投入金额（容量判据）

    # ---- 时间与重叠 ----
    plan_bar: int = -1
    entry_bar: int = -1
    exit_bar: int = -1
    entry_date: str = ""
    exit_date: str = ""
    overlaps_prior: bool = False

    # ---- 标的标识 ----
    # 因子验证报告要能回答「这批样本来自哪些股票」。原先不记录，于是报告里
    # panel.codes 恒为空列表 —— 「用 ≥30 只股票池重跑验证」这条验收标准
    # 根本无法被审计（看不出样本是哪来的）。
    code: str = ""
    name: str = ""

    def outcome_label(self, dead_zone: float = 0.3) -> Optional[int]:
        """标定用的 0/1 标签。``None`` 表示该样本不参与拟合。

        规则：
          * ``not_entered``（价格从未进区间）→ ``None``。这是「没执行」而不是
            「亏了」，算作 0 会系统性压低概率。代价是会引入选择偏差：高置信度的
            计划通常现价就在区间内，更容易成交——分组看时需注意。
          * ``|return_pct| < dead_zone`` → ``None``。落在交易成本噪声带内的结果
            不携带方向信息，留着只会给标签加噪。A 股双边成本约 0.1%~0.2%，默认
            取 0.3% 覆盖印花税 + 佣金 + 滑点。
          * 其余：盈利为 1，亏损为 0。
        """
        if not self.entry_executed or self.exit_reason in ("not_entered", "overlap_skipped"):
            return None
        if self.confidence is None:
            return None
        if abs(self.return_pct) < dead_zone:
            return None
        return 1 if self.return_pct > 0 else 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class BacktestResult:
    """回测总结果。"""

    trades: list = field(default_factory=list)
    metrics: Optional[BacktestMetrics] = None
    portfolio: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trades": [t.to_dict() if isinstance(t, BacktestTrade) else t for t in self.trades],
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "portfolio": self.portfolio,
        }


def _resolve_trade_dates(df: pd.DataFrame) -> Optional[list]:
    """抽出与每根K线一一对应的日期字符串列表，失败返回 ``None``。

    ⚠️ 必须在 ``reset_index(drop=True)`` **之前**调用：回测内部会把 index 重置成
    RangeIndex，之后 ``df.index[i]`` 就只剩整数序号了（而 ``pd.Timestamp(0)``
    不报错、会静默解析成 1970-01-01，比抛异常更难发现）。

    ``data_fetcher.fetch_kline`` 返回的是 DatetimeIndex、没有 ``date`` 列，所以必须
    支持从 index 取日期；否则所有交易日期退化成序号，多股票拼接后时间序错乱，
    时序分块交叉验证会变成「按股票分块」。
    """
    if "date" in df.columns:
        try:
            return pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").tolist()
        except (ValueError, TypeError, AttributeError):
            pass
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index.strftime("%Y-%m-%d").tolist()
    return None


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


class TradingPlanBacktest:
    """对单只股票的历史K线回测 Trading Plan 规则。

    Args:
        engine: 机会引擎实例（复用其账户/风控配置）；None 时用默认引擎。
        min_rr: 仅对 RR >= 该值的计划视为可交易（防低质信号入账）。
        max_hold_days: 最长持有交易日数（超时按最后收盘离场）。
        stride: 每隔 N 个交易日生成一个计划（降低重叠，默认每天）。
        exec_config: 成交假设（成本 / 涨跌停 / 停牌 / 容量）。None 时用 A 股默认。
        overlap_policy: ``independent``（默认，逐笔统计并标记重叠）或
            ``sequential``（前一笔未平仓前不开新仓）。
        market: 市场（CN/HK/US），用于涨跌停幅度与成本预设。
        initial_capital: 组合净值曲线的初始资金。
        max_positions: 组合口径下的最大并发持仓数。
        gate: 质量闸门配置。None → ``QualityGateConfig.for_backtest()``
            （放宽「数据完整性」类规则，因为历史链路拿不到财报；保留分数与 RR
            门槛，使回测验证的是「闸门 + 价格规则」的整体效果）。
            传 ``QualityGateConfig.disabled()`` 可复现闸门上线前的旧口径。
    """

    def __init__(
        self,
        engine: Optional[OpportunityEngine] = None,
        *,
        min_rr: float = 1.5,
        max_hold_days: int = 60,
        stride: int = 1,
        exec_config: Optional[ExecutionConfig] = None,
        overlap_policy: str = "independent",
        market: str = "CN",
        initial_capital: float = 100_000.0,
        max_positions: int = 5,
        gate: Optional[QualityGateConfig] = None,
    ) -> None:
        self.engine = engine or OpportunityEngine()
        self.min_rr = min_rr
        self.max_hold_days = max_hold_days
        self.stride = max(stride, 1)
        self.market = str(market or "CN").upper()
        self.exec_config = apply_market(exec_config or ExecutionConfig(), self.market)
        if overlap_policy not in ("independent", "sequential"):
            raise ValueError(f"未知 overlap_policy: {overlap_policy!r}")
        self.overlap_policy = overlap_policy
        self.initial_capital = initial_capital
        self.max_positions = max(1, int(max_positions))
        # 回测默认用放宽版闸门（见 docstring）。引擎的闸门配置被覆盖，保证
        # 「回测口径」与「实盘口径」的差异是显式的、可复现的，而不是隐式的。
        self.gate = gate if gate is not None else QualityGateConfig.for_backtest()
        self.engine.quality_gate = self.gate

    # ------------------------------------------------------------------ #
    def run(self, df: pd.DataFrame, code: str = "", name: str = "") -> BacktestResult:
        """执行回测。df 需为已加指标的日K（至少 ~130 行）。

        计划在 T 日生成（只用截至 T 的数据），随后在 T+1 起逐日模拟。
        """
        if df is None or len(df) < 130:
            return BacktestResult()

        # 日期必须在 reset_index 之前抽取（重置后 index 只剩序号）
        dates = _resolve_trade_dates(df)
        d = df.reset_index(drop=True)
        trades: list[BacktestTrade] = []

        closes = pd.to_numeric(d["close"], errors="coerce").tolist()
        # 涨跌停幅度只对 A 股有意义：港股/美股没有 A 股式日内涨跌停，
        # 且 exec_config.with_market() 已把非 CN 的 enforce_price_limits 关掉，
        # 这里置 0 是双保险（避免任何路径残留 10% 口径）。
        limit_pct = (
            limit_pct_for_code(code, is_st=self.exec_config.is_st)
            if self.market == "CN" and self.exec_config.enforce_price_limits
            else 0.0
        )
        last_exit_bar = -1

        # 从第 120 根K线起生成计划（引擎内部需至少 30 根 + 指标预热）
        for i in range(120, len(d) - 1, self.stride):
            hist = d.iloc[: i + 1]  # 截至 T 日，含 T
            res = self.engine.analyze(code, name, hist)
            if res.plan is None:
                continue
            p = res.plan
            if p.decision in (DecisionState.AVOID, DecisionState.SELL):
                continue
            if p.risk_reward_1 is None or p.risk_reward_1 < self.min_rr:
                continue
            if not p.entry_high or not p.entry_low or not p.stop_loss:
                continue

            trade = BacktestTrade(
                date=dates[i] if dates is not None and i < len(dates) else str(i),
                decision=p.decision.value,
                entry_low=p.entry_low,
                entry_price=p.entry_price or p.entry_low,
                entry_high=p.entry_high,
                stop_loss=p.stop_loss,
                target_1=p.target_1 or 0.0,
                target_2=p.target_2 or 0.0,
                # 标的标识：因子验证报告靠它记录股票池
                code=code,
                name=name,
                # 计划生成时点的打分快照：标定与归因的输入。
                # 优先取 meta["confidence_raw"]（引擎启用标定器时会写入未标定值），
                # 这样即使有人把带标定器的引擎传进回测，这里记录的仍是原始打分——
                # 否则「用已标定的分数再去拟合标定」会变成自我强化的循环。
                confidence=p.meta.get("confidence_raw", p.confidence),
                opportunity_score=p.opportunity_score,
                stock_score=p.stock_score,
                risk_reward_1=p.risk_reward_1,
                plan_bar=i,
            )
            if res.position is not None and res.position.position_amount:
                trade.planned_position_amount = float(res.position.position_amount)

            trade.overlaps_prior = i <= last_exit_bar
            if self.overlap_policy == "sequential" and trade.overlaps_prior:
                trade.exit_reason = "overlap_skipped"
                trade.exit_bar = last_exit_bar
                trades.append(trade)
                continue

            # 模拟：从 T+1 起逐日
            future = d.iloc[i + 1 : i + 1 + self.max_hold_days]
            ctx = {
                "config": self.exec_config,
                "limit_pct": limit_pct,
                # 第 j 根 future 的「前收盘」= d[i+j] 的收盘
                "prev_closes": closes[i : i + len(future)],
                "future_dates": (
                    dates[i + 1 : i + 1 + len(future)] if dates is not None else []
                ),
                "base_bar": i,
                "plan_date": trade.date,
            }
            self._simulate(trade, future, ctx)
            trades.append(trade)
            if trade.entry_executed and trade.exit_bar >= 0:
                last_exit_bar = max(last_exit_bar, trade.exit_bar)

        portfolio = simulate_portfolio(
            [t.to_dict() for t in trades],
            initial_capital=self.initial_capital,
            max_position_pct=self.exec_config.max_position_pct,
            max_positions=self.max_positions,
        )
        metrics = calc_metrics(
            sample_size=len(trades),
            entry_zone_hits=sum(1 for t in trades if t.entry_executed),
            trades=[t.to_dict() for t in trades],
            initial_capital=self.initial_capital,
            portfolio=portfolio,
            max_position_pct=self.exec_config.max_position_pct,
            max_positions=self.max_positions,
        )
        return BacktestResult(trades=trades, metrics=metrics, portfolio=portfolio)

    # ------------------------------------------------------------------ #
    def _simulate(self, trade: BacktestTrade, future: pd.DataFrame, ctx: Optional[dict] = None) -> None:
        """在 T 日之后逐日模拟：入场 → 止损 / 目标 / 超时。

        成交规则（见模块 docstring）：
          * 入场必须真的触及委托价，否则顺延；涨停封板 / 停牌日不成交。
          * 止损按 ``min(开盘价, 止损价)``、目标按 ``max(开盘价, 目标价)`` 成交。
          * 同日同时触及止损与目标 → 按止损处理（保守）。
          * 逐笔扣除交易成本；容量不足时按比例缩量并记录 ``capital_weight``。
        """
        if future is None or future.empty:
            return
        ctx = ctx or {}
        cfg: ExecutionConfig = ctx.get("config") or self.exec_config
        cost = cfg.cost_model
        limit_pct = float(ctx.get("limit_pct", LIMIT_PCT_DEFAULT))
        prev_closes = ctx.get("prev_closes") or []
        future_dates = ctx.get("future_dates") or []
        base_bar = int(ctx.get("base_bar", -1))

        open_ = pd.to_numeric(future["open"], errors="coerce")
        high = pd.to_numeric(future["high"], errors="coerce")
        low = pd.to_numeric(future["low"], errors="coerce")
        close = pd.to_numeric(future["close"], errors="coerce")
        volume = pd.to_numeric(future["volume"], errors="coerce") if "volume" in future.columns else None
        amount = pd.to_numeric(future["amount"], errors="coerce") if "amount" in future.columns else None

        entry_exec_price: Optional[float] = None
        entry_day: Optional[int] = None

        for j in range(len(future)):
            o, h, lo, cl = (_f(open_.iloc[j]), _f(high.iloc[j]), _f(low.iloc[j]), _f(close.iloc[j]))
            if None in (o, h, lo, cl):
                continue
            prev_c = _f(prev_closes[j]) if j < len(prev_closes) else None
            vol = _f(volume.iloc[j]) if volume is not None else None
            amt = _f(amount.iloc[j]) if amount is not None else None

            if cfg.enforce_suspension and is_suspended(vol, amt):
                if entry_exec_price is None:
                    trade.limit_blocked_days += 1
                continue

            locked = (
                price_limit_state(prev_c, h, lo, limit_pct=limit_pct)
                if cfg.enforce_price_limits
                else 0
            )

            if entry_exec_price is None:
                if locked == 1:
                    # 一字涨停：买不进，顺延
                    trade.limit_blocked_days += 1
                    continue
                fill = self._entry_fill(o, lo, trade, cfg)
                if fill is None:
                    continue
                entry_exec_price = fill[0]
                entry_day = j
                trade.entry_executed = True
                trade.entry_exec_price = round(entry_exec_price, 2)
                trade.fill_type = fill[1]
                trade.days_to_entry = j
                trade.entry_bar = base_bar + 1 + j
                trade.entry_date = future_dates[j] if j < len(future_dates) else ""
                self._apply_capacity(trade, cl, vol, amt, cfg)
                trade.hit_target_1 = h >= trade.target_1
                trade.hit_target_2 = h >= trade.target_2

                # 同日检查止损 / 目标（保守：止损优先）
                hit = self._check_stop(o, lo, trade, cfg)
                if hit is not None:
                    self._close(trade, hit[0], "stop_loss", 1, j, future_dates,
                                base_bar, cost, gapped=hit[1])
                    return
                hit = self._check_target(o, h, trade, cfg)
                if hit is not None:
                    self._close(trade, hit[0], "target_2", 1, j, future_dates,
                                base_bar, cost, gapped=hit[1])
                    return
                continue

            # 已入场：逐日检查止损 / 目标2
            trade.hit_target_1 = trade.hit_target_1 or h >= trade.target_1
            trade.hit_target_2 = trade.hit_target_2 or h >= trade.target_2

            # 一字跌停：卖不出，止损/目标当日都无法执行，顺延到下一可交易日
            if locked == -1:
                continue

            hit = self._check_stop(o, lo, trade, cfg)
            if hit is not None:
                self._close(trade, hit[0], "stop_loss", j - entry_day + 1, j, future_dates,
                            base_bar, cost, gapped=hit[1])
                return
            hit = self._check_target(o, h, trade, cfg)
            if hit is not None:
                self._close(trade, hit[0], "target_2", j - entry_day + 1, j, future_dates,
                            base_bar, cost, gapped=hit[1])
                return

        # 未触发止损/目标2：按最后收盘离场（超时）
        if entry_exec_price is not None:
            last_close = _f(close.iloc[-1])
            last_j = len(future) - 1
            if last_close is None:
                trade.exit_reason = "timeout"
                trade.holding_days = len(future) - entry_day
                trade.return_pct = 0.0
                return
            self._close(trade, last_close, "timeout", len(future) - entry_day, last_j,
                        future_dates, base_bar, cost, gapped=False)
        # 全程未进入入场区
        else:
            trade.exit_reason = "not_entered"
            trade.return_pct = 0.0

    # ------------------------------------------------------------------ #
    @staticmethod
    def _entry_fill(o: float, lo: float, trade: BacktestTrade, cfg: ExecutionConfig):
        """限价买入的可成交性判定 → ``(成交价, 成交类型)`` 或 ``None``。

        * ``open <= 委托价`` → 以开盘价成交（价格改善），类型 ``open``
        * 否则 ``low <= 委托价`` → 盘中触及委托价，以委托价成交，类型 ``limit``
        * 否则当日不可达（最低价仍高于委托价），不成交

        注意这里**不再**把成交价夹到 ``entry_low`` 之上：原实现用
        ``max(fill, entry_low)`` 强行把成交价抬到区间下沿，既非真实成交价，
        又会与「幽灵成交」叠加造成双向偏差。
        """
        limit = trade.entry_price
        if limit is None or limit <= 0:
            return None
        tol = max(cfg.eps, 1e-6)
        if o <= limit * (1 + tol):
            return float(o), "open"
        if lo <= limit * (1 + tol):
            return float(limit), "limit"
        return None

    @staticmethod
    def _check_stop(o: float, lo: float, trade: BacktestTrade, cfg: ExecutionConfig):
        """止损检查 → ``(成交价, 是否跳空)`` 或 ``None``。"""
        stop = trade.stop_loss
        if not stop or lo > stop:
            return None
        if cfg.gap_aware and o < stop:
            return float(o), True
        return float(stop), False

    @staticmethod
    def _check_target(o: float, h: float, trade: BacktestTrade, cfg: ExecutionConfig):
        """目标2检查 → ``(成交价, 是否跳空)`` 或 ``None``。"""
        t2 = trade.target_2
        if not t2 or h < t2:
            return None
        if cfg.gap_aware and o > t2:
            return float(o), True
        return float(t2), False

    def _apply_capacity(
        self,
        trade: BacktestTrade,
        close_price: float,
        vol: Optional[float],
        amt: Optional[float],
        cfg: ExecutionConfig,
    ) -> None:
        """容量约束：单日最多吃掉当日成交额的 ``participation_rate``。"""
        planned = trade.planned_position_amount
        if not planned or planned <= 0:
            trade.capacity_fill_ratio = 1.0
            trade.capacity_limited = False
            trade.capital_weight = 1.0
            return
        day_amount = amt if (amt and amt > 0) else None
        if day_amount is None and vol and vol > 0 and close_price > 0:
            day_amount = vol * close_price
        if not day_amount:
            ratio = 1.0
        else:
            ratio = min(1.0, (cfg.participation_rate * day_amount) / planned)
        ratio = max(0.0, min(1.0, ratio))
        trade.capacity_fill_ratio = round(ratio, 4)
        trade.capacity_limited = ratio < 0.999
        trade.capital_weight = trade.capacity_fill_ratio

    @staticmethod
    def _close(
        trade: BacktestTrade,
        exit_price: float,
        reason: str,
        holding_days: int,
        j: int,
        future_dates: list,
        base_bar: int,
        cost,
        *,
        gapped: bool,
    ) -> None:
        """结算一笔交易：计算毛收益、成本与净收益。"""
        entry = trade.entry_exec_price
        if not entry or entry <= 0:
            trade.exit_reason = reason
            return
        gross = (exit_price / entry - 1) * 100.0
        # 订单金额用于最低佣金判定：计划金额缺失时退回「成交价 × 1 手」，
        # 保证小额场景下最低佣金仍被计入（缺失就跳过会系统性低估成本）。
        notional = trade.planned_position_amount or (entry * 100.0)
        cost_pct = cost.round_trip_cost_pct(entry, exit_price, notional=notional)
        trade.gross_return_pct = round(gross, 3)
        trade.cost_pct = round(cost_pct, 3)
        trade.min_commission_pct = round(cost.min_commission_pct(notional), 3)
        trade.return_pct = round(gross - cost_pct, 2)
        trade.exit_reason = reason
        trade.holding_days = int(holding_days)
        trade.gapped = bool(gapped)
        trade.exit_bar = base_bar + 1 + j
        trade.exit_date = future_dates[j] if j < len(future_dates) else ""
        if reason == "target_2":
            trade.hit_target_2 = True
