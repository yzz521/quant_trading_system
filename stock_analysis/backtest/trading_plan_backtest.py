"""Trading Plan 历史回测引擎。

验证「入场区间 / 止损 / 目标价」规则在历史上是否有效（计划书 §17）。

严格防 look-ahead bias：
  * 生成计划：只用截至 T 日的K线（``df.iloc[:i+1]``）→ 引擎内部只看到历史
  * 评估结果：只用 T 日之后的数据（入场触发、止损/目标命中、收益）
  * 指标（MA/ATR/BOLL 等）均为因果计算，不引入未来值
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..opportunity.opportunity_engine import OpportunityEngine
from ..opportunity.trading_plan import DecisionState
from .metrics import BacktestMetrics, calc_metrics


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

    entry_executed: bool = False      # 后续价格是否进入入场区
    entry_exec_price: float = 0.0
    exit_reason: str = ""             # stop_loss / target_2 / timeout / not_entered
    return_pct: float = 0.0
    holding_days: int = 0
    hit_target_1: bool = False
    hit_target_2: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class BacktestResult:
    """回测总结果。"""

    trades: list = field(default_factory=list)
    metrics: Optional[BacktestMetrics] = None

    def to_dict(self) -> dict:
        return {
            "trades": [t.to_dict() if isinstance(t, BacktestTrade) else t for t in self.trades],
            "metrics": self.metrics.to_dict() if self.metrics else None,
        }


class TradingPlanBacktest:
    """对单只股票的历史K线回测 Trading Plan 规则。

    Args:
        engine: 机会引擎实例（复用其账户/风控配置）；None 时用默认引擎。
        min_rr: 仅对 RR >= 该值的计划视为可交易（防低质信号入账）。
        max_hold_days: 最长持有交易日数（超时按最后收盘离场）。
        stride: 每隔 N 个交易日生成一个计划（降低重叠，默认每天）。
    """

    def __init__(
        self,
        engine: Optional[OpportunityEngine] = None,
        *,
        min_rr: float = 1.5,
        max_hold_days: int = 60,
        stride: int = 1,
    ) -> None:
        self.engine = engine or OpportunityEngine()
        self.min_rr = min_rr
        self.max_hold_days = max_hold_days
        self.stride = max(stride, 1)

    # ------------------------------------------------------------------ #
    def run(self, df: pd.DataFrame, code: str = "", name: str = "") -> BacktestResult:
        """执行回测。df 需为已加指标的日K（至少 ~130 行）。

        计划在 T 日生成（只用截至 T 的数据），随后在 T+1 起逐日模拟。
        """
        if df is None or len(df) < 130:
            return BacktestResult()

        d = df.reset_index(drop=True)
        trades: list[BacktestTrade] = []

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
                date=str(pd.Timestamp(d.iloc[i]["date"]).date()) if "date" in d.columns else str(i),
                decision=p.decision.value,
                entry_low=p.entry_low,
                entry_price=p.entry_price or p.entry_low,
                entry_high=p.entry_high,
                stop_loss=p.stop_loss,
                target_1=p.target_1 or 0.0,
                target_2=p.target_2 or 0.0,
            )

            # 模拟：从 T+1 起逐日
            future = d.iloc[i + 1 : i + 1 + self.max_hold_days]
            self._simulate(trade, future)
            trades.append(trade)

        metrics = calc_metrics(
            sample_size=len(trades),
            entry_zone_hits=sum(1 for t in trades if t.entry_executed),
            trades=[t.to_dict() for t in trades],
        )
        return BacktestResult(trades=trades, metrics=metrics)

    # ------------------------------------------------------------------ #
    def _simulate(self, trade: BacktestTrade, future: pd.DataFrame) -> None:
        """在 T 日之后逐日模拟：入场 → 止损 / 目标 / 超时。"""
        if future is None or future.empty:
            return

        open_ = future["open"].astype(float)
        high = future["high"].astype(float)
        low = future["low"].astype(float)
        close = future["close"].astype(float)

        entry_exec_price = None
        entry_day = None

        for j in range(len(future)):
            o = float(open_.iloc[j])
            h = float(high.iloc[j])
            lo = float(low.iloc[j])

            # 入场判定：当日价格进入入场区（low <= entry_high）
            if entry_exec_price is None:
                if lo <= trade.entry_high:
                    # 以开盘价成交（若开盘已在区间内），否则以标准入场价
                    entry_exec_price = min(o, trade.entry_price) if o <= trade.entry_high else trade.entry_price
                    entry_exec_price = max(entry_exec_price, trade.entry_low)
                    entry_day = j
                    trade.entry_executed = True
                    trade.entry_exec_price = round(entry_exec_price, 2)
                    # 同日检查止损/目标
                    trade.hit_target_1 = h >= trade.target_1
                    trade.hit_target_2 = h >= trade.target_2
                    if lo <= trade.stop_loss:
                        trade.exit_reason = "stop_loss"
                        trade.holding_days = 1
                        trade.return_pct = round((trade.stop_loss / entry_exec_price - 1) * 100, 2)
                        return
                    if h >= trade.target_2:
                        trade.exit_reason = "target_2"
                        trade.holding_days = 1
                        trade.return_pct = round((trade.target_2 / entry_exec_price - 1) * 100, 2)
                        return
                continue

            # 已入场：逐日检查止损 / 目标2
            trade.hit_target_1 = trade.hit_target_1 or h >= trade.target_1
            trade.hit_target_2 = trade.hit_target_2 or h >= trade.target_2
            if lo <= trade.stop_loss:
                trade.exit_reason = "stop_loss"
                trade.holding_days = j - entry_day + 1
                trade.return_pct = round((trade.stop_loss / entry_exec_price - 1) * 100, 2)
                return
            if h >= trade.target_2:
                trade.exit_reason = "target_2"
                trade.holding_days = j - entry_day + 1
                trade.return_pct = round((trade.target_2 / entry_exec_price - 1) * 100, 2)
                return

        # 未触发止损/目标2：按最后收盘离场（超时）
        if entry_exec_price is not None:
            trade.exit_reason = "timeout"
            trade.holding_days = len(future) - entry_day
            last_close = float(close.iloc[-1])
            trade.return_pct = round((last_close / entry_exec_price - 1) * 100, 2)
        # 全程未进入入场区
        else:
            trade.exit_reason = "not_entered"
            trade.return_pct = 0.0
