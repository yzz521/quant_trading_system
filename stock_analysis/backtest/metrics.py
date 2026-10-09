"""Trading Plan 回测指标聚合。

计划书 §17 要求的指标：
  sample_size / entry_zone_hit_rate / stop_loss_trigger_rate / target_1_hit_rate /
  target_2_hit_rate / win_rate / avg_return / max_drawdown / avg_holding_period

2026-09 起补充「成交真实性」口径（见 ``backtest/execution.py``）：
  net_avg_return（扣费后）/ gross_avg_return / cost_drag / capacity_limited_rate /
  avg_fill_ratio / overlap_rate / gapped_stop_rate / not_entered

以及**组合口径**（``simulate_portfolio``）：按现金与持仓逐日结算，取代
「把重叠信号当独立交易逐笔复利」的乐观算法。

口径说明
--------
* ``win_rate`` / ``avg_return`` 只统计**真正成交**（``entry_executed``）的交易；
  ``not_entered``（价格从未进区间）单独计数。把未成交当 0 收益会系统性压低胜率。
* ``return_pct`` 为**扣费后净收益**；``gross_return_pct`` 为扣费前收益；
  两者之差即 ``cost_drag``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np


@dataclass
class BacktestMetrics:
    """回测结果指标。"""

    sample_size: int = 0                # 生成的历史交易计划数
    entry_zone_hit_rate: float = 0.0    # 价格进入入场区的比例
    stop_loss_trigger_rate: float = 0.0 # 止损触发比例（含入场前即破位）
    target_1_hit_rate: float = 0.0      # 达到 T1 的比例
    target_2_hit_rate: float = 0.0      # 达到 T2 的比例
    win_rate: float = 0.0               # 盈利交易占比
    avg_return: float = 0.0             # 平均单笔**净**收益率（%，扣费后）
    max_drawdown: float = 0.0           # 策略净值最大回撤（%，逐笔复利口径）
    avg_holding_period: float = 0.0     # 平均持有交易日数
    total_trades: int = 0               # 实际成交的计划数
    profitable_trades: int = 0
    stop_loss_trades: int = 0
    target1_trades: int = 0

    # ---- 成交真实性口径 ----
    not_entered: int = 0                # 价格从未进入入场区（未成交）
    executed_trades: int = 0            # 成交笔数（= total_trades，显式命名）
    gross_avg_return: float = 0.0       # 平均单笔毛收益（%，扣费前）
    cost_drag: float = 0.0              # 平均单笔成本侵蚀（百分点）
    capacity_limited_rate: float = 0.0  # 受容量约束（缩量）的成交占比
    avg_fill_ratio: float = 1.0         # 平均可成交比例（1.0 = 未受容量限制）
    overlap_rate: float = 0.0           # 信号与前一笔持仓窗口重叠的比例
    gapped_stop_rate: float = 0.0       # 止损中因跳空以更差价成交的比例
    limit_blocked_trades: int = 0       # 因涨停封板/停牌而延迟入场的计划数

    # ---- 组合口径（现金 + 持仓逐日结算）----
    portfolio_total_return: float = 0.0
    portfolio_cagr: float = 0.0
    portfolio_max_drawdown: float = 0.0
    portfolio_final_equity: float = 0.0
    portfolio_skipped: int = 0          # 因满仓/无现金/并发上限被放弃的信号
    avg_concurrent_positions: float = 0.0

    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sample_size": self.sample_size,
            "entry_zone_hit_rate": round(self.entry_zone_hit_rate, 4),
            "stop_loss_trigger_rate": round(self.stop_loss_trigger_rate, 4),
            "target_1_hit_rate": round(self.target_1_hit_rate, 4),
            "target_2_hit_rate": round(self.target_2_hit_rate, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_return": round(self.avg_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_holding_period": round(self.avg_holding_period, 2),
            "total_trades": self.total_trades,
            "profitable_trades": self.profitable_trades,
            "stop_loss_trades": self.stop_loss_trades,
            "target1_trades": self.target1_trades,
            "not_entered": self.not_entered,
            "executed_trades": self.executed_trades,
            "gross_avg_return": round(self.gross_avg_return, 4),
            "cost_drag": round(self.cost_drag, 4),
            "capacity_limited_rate": round(self.capacity_limited_rate, 4),
            "avg_fill_ratio": round(self.avg_fill_ratio, 4),
            "overlap_rate": round(self.overlap_rate, 4),
            "gapped_stop_rate": round(self.gapped_stop_rate, 4),
            "limit_blocked_trades": self.limit_blocked_trades,
            "portfolio_total_return": round(self.portfolio_total_return, 4),
            "portfolio_cagr": round(self.portfolio_cagr, 4),
            "portfolio_max_drawdown": round(self.portfolio_max_drawdown, 4),
            "portfolio_final_equity": round(self.portfolio_final_equity, 2),
            "portfolio_skipped": self.portfolio_skipped,
            "avg_concurrent_positions": round(self.avg_concurrent_positions, 3),
            "evidence": self.evidence,
        }


# --------------------------------------------------------------------------- #
# 组合净值曲线
# --------------------------------------------------------------------------- #
def _parse_date(x) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def simulate_portfolio(
    trades: list[dict],
    *,
    initial_capital: float = 100_000.0,
    max_position_pct: float = 0.20,
    max_positions: int = 5,
) -> dict:
    """按现金与持仓做组合级结算，得到**可实现**的净值曲线。

    与「逐笔复利」的区别：同一时点只能持有限定数量的仓位，资金被占用期间新信号
    若拿不到现金就会被放弃（计入 ``skipped``），因此不会出现「同一笔钱同时买 5 只票」。

    持仓在持有期内按**成本价**计价（回测只记录了最终出场价，没有逐日盯市数据），
    因此净值曲线是「已实现权益」口径：回撤反映的是已实现亏损，不包含持有期内的
    浮动回撤 —— 这一点比原口径保守性略低，但消除了资金重复使用的高估。
    """
    evs = []
    for t in trades:
        if not t.get("entry_executed"):
            continue
        if t.get("exit_reason") == "overlap_skipped":
            continue
        ed = _parse_date(t.get("entry_date"))
        xd = _parse_date(t.get("exit_date"))
        if ed is None or xd is None:
            continue
        evs.append((ed, xd, t))
    if not evs:
        return {}

    evs.sort(key=lambda e: (e[0], e[1]))
    cash = float(initial_capital)
    open_pos: list[tuple[date, float]] = []   # (exit_date, 成本市值)
    curve: list[tuple[date, float]] = []
    skipped = 0
    concurrent: list[int] = []

    def _release(upto: date) -> None:
        nonlocal cash, open_pos
        keep = []
        for xd, val in open_pos:
            if xd <= upto:
                cash += val
            else:
                keep.append((xd, val))
        open_pos = keep

    def _equity() -> float:
        return cash + sum(v for _, v in open_pos)

    for ed, xd, t in evs:
        _release(ed)
        if len(open_pos) >= max_positions or cash <= 0:
            skipped += 1
            curve.append((ed, _equity()))
            continue
        equity_now = _equity()
        notional = min(equity_now * max_position_pct, cash)
        w = t.get("capital_weight")
        w = 1.0 if w is None else float(w)
        notional *= max(0.0, min(1.0, w))
        if notional <= 0:
            skipped += 1
            curve.append((ed, _equity()))
            continue
        cash -= notional
        ret = float(t.get("return_pct") or 0.0)
        open_pos.append((xd, notional * (1 + ret / 100.0)))
        concurrent.append(len(open_pos))
        curve.append((ed, _equity()))

    # 收尾：释放全部持仓
    for xd, val in open_pos:
        cash += val
    open_pos = []
    last_date = max(xd for _, xd, _ in evs)
    curve.append((last_date, cash))
    curve.sort(key=lambda c: c[0])

    peak = float(initial_capital)
    max_dd = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)

    total_ret = (cash / initial_capital - 1) * 100.0 if initial_capital else 0.0
    span_days = (curve[-1][0] - curve[0][0]).days if len(curve) >= 2 else 0
    cagr = 0.0
    if span_days > 0 and initial_capital > 0 and cash > 0:
        cagr = ((cash / initial_capital) ** (365.0 / span_days) - 1) * 100.0

    return {
        "total_return": total_ret,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "final_equity": cash,
        "skipped": skipped,
        "avg_concurrent_positions": float(np.mean(concurrent)) if concurrent else 0.0,
        "n_trades": len(concurrent),
        "span_days": span_days,
        "equity_curve": [(d.isoformat(), round(v, 2)) for d, v in curve],
    }


# --------------------------------------------------------------------------- #
# 指标聚合
# --------------------------------------------------------------------------- #
def calc_metrics(
    *,
    sample_size: int,
    entry_zone_hits: int,
    trades: list[dict],
    initial_capital: float = 100_000.0,
    portfolio: Optional[dict] = None,
    max_position_pct: float = 0.20,
    max_positions: int = 5,
) -> BacktestMetrics:
    """从回测记录聚合指标。

    Args:
        sample_size: 生成计划的样本数。
        entry_zone_hits: 价格进入入场区的计划数。
        trades: 交易记录列表，每项含
            exit_reason("stop_loss"/"target_2"/"timeout"/"not_entered")、
            return_pct（净收益）、holding_days、hit_target_1、hit_target_2。
        initial_capital: 净值曲线初始资金。
        portfolio: 预计算的组合结果（``simulate_portfolio``）；None 时按 trades 现算。
        max_position_pct / max_positions: 组合结算参数。
    """
    m = BacktestMetrics()
    m.sample_size = max(sample_size, 0)
    m.entry_zone_hit_rate = entry_zone_hits / sample_size if sample_size else 0.0

    if not trades:
        return m

    # 兼容手工构造的 trades（无 entry_executed 字段）→ 视为全部成交
    has_flag = any("entry_executed" in t for t in trades)
    if has_flag:
        executed = [t for t in trades if t.get("entry_executed")]
    else:
        executed = list(trades)

    m.not_entered = len(trades) - len(executed)
    m.total_trades = len(executed)
    m.executed_trades = len(executed)

    # 重叠率按「全部计划」统计（含未成交），因为重叠是信号层面的问题
    m.overlap_rate = (
        sum(1 for t in trades if t.get("overlaps_prior")) / len(trades) if trades else 0.0
    )
    m.limit_blocked_trades = sum(1 for t in trades if t.get("limit_blocked_days"))

    if not executed:
        return m

    returns = [t.get("return_pct") or 0.0 for t in executed]
    gross = [t.get("gross_return_pct") for t in executed]
    holds = [t.get("holding_days") or 0 for t in executed]
    t1_hits = sum(1 for t in executed if t.get("hit_target_1"))
    t2_hits = sum(1 for t in executed if t.get("hit_target_2"))
    stop_hits = sum(1 for t in executed if t.get("exit_reason") == "stop_loss")
    wins = sum(1 for r in returns if r > 0)

    m.stop_loss_trigger_rate = stop_hits / len(executed)
    m.target_1_hit_rate = t1_hits / len(executed)
    m.target_2_hit_rate = t2_hits / len(executed)
    m.win_rate = wins / len(executed)
    m.avg_return = float(np.mean(returns))
    m.avg_holding_period = float(np.mean(holds))
    m.stop_loss_trades = stop_hits
    m.target1_trades = t1_hits
    m.profitable_trades = wins

    if any(g is not None for g in gross):
        gv = [float(g) for g in gross if g is not None]
        if gv:
            m.gross_avg_return = float(np.mean(gv))
            m.cost_drag = m.gross_avg_return - m.avg_return

    ratios = [float(t.get("capacity_fill_ratio")) for t in executed
              if t.get("capacity_fill_ratio") is not None]
    if ratios:
        m.avg_fill_ratio = float(np.mean(ratios))
        m.capacity_limited_rate = sum(1 for r in ratios if r < 0.999) / len(ratios)

    sl = [t for t in executed if t.get("exit_reason") == "stop_loss"]
    if sl:
        m.gapped_stop_rate = sum(1 for t in sl if t.get("gapped")) / len(sl)

    # 逐笔复利口径（保留：与历史报告可比；不是可实现收益）
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    for r in returns:
        equity *= 1 + r / 100.0
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
    m.max_drawdown = max_dd

    # 组合口径（可实现）
    if portfolio is None:
        portfolio = simulate_portfolio(
            executed,
            initial_capital=initial_capital,
            max_position_pct=max_position_pct,
            max_positions=max_positions,
        )
    if portfolio:
        m.portfolio_total_return = float(portfolio.get("total_return", 0.0))
        m.portfolio_cagr = float(portfolio.get("cagr", 0.0))
        m.portfolio_max_drawdown = float(portfolio.get("max_drawdown", 0.0))
        m.portfolio_final_equity = float(portfolio.get("final_equity", initial_capital))
        m.portfolio_skipped = int(portfolio.get("skipped", 0))
        m.avg_concurrent_positions = float(portfolio.get("avg_concurrent_positions", 0.0))

    m.evidence = {
        "initial_capital": initial_capital,
        "final_equity": round(equity, 2),
        "final_equity_portfolio": round(m.portfolio_final_equity, 2),
        "overlap_note": "逐笔复利口径假设同一资金可同时用于多笔重叠交易，不可实现；"
                        "portfolio_* 为按现金与持仓结算的可实现口径。",
    }
    return m
