"""Trading Plan 回测指标聚合。

计划书 §17 要求的指标：
  sample_size / entry_zone_hit_rate / stop_loss_trigger_rate / target_1_hit_rate /
  target_2_hit_rate / win_rate / avg_return / max_drawdown / avg_holding_period
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    avg_return: float = 0.0             # 平均单笔收益率（%）
    max_drawdown: float = 0.0           # 策略净值最大回撤（%）
    avg_holding_period: float = 0.0     # 平均持有交易日数
    total_trades: int = 0               # 实际成交的计划数
    profitable_trades: int = 0
    stop_loss_trades: int = 0
    target1_trades: int = 0
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
            "evidence": self.evidence,
        }


def calc_metrics(
    *,
    sample_size: int,
    entry_zone_hits: int,
    trades: list[dict],
    initial_capital: float = 100_000.0,
) -> BacktestMetrics:
    """从回测记录聚合指标。

    Args:
        sample_size: 生成计划的样本数。
        entry_zone_hits: 价格进入入场区的计划数。
        trades: 已成交的交易记录列表，每项含
            exit_reason("stop_loss"/"target_1"/"target_2"/"timeout")、
            return_pct、holding_days、hit_target_1、hit_target_2。
        initial_capital: 净值曲线初始资金。
    """
    m = BacktestMetrics()
    m.sample_size = max(sample_size, 0)
    m.entry_zone_hit_rate = entry_zone_hits / sample_size if sample_size else 0.0

    if not trades:
        return m

    m.total_trades = len(trades)
    returns = [t.get("return_pct") or 0.0 for t in trades]
    holds = [t.get("holding_days") or 0 for t in trades]
    t1_hits = sum(1 for t in trades if t.get("hit_target_1"))
    t2_hits = sum(1 for t in trades if t.get("hit_target_2"))
    stop_hits = sum(1 for t in trades if t.get("exit_reason") == "stop_loss")
    wins = sum(1 for r in returns if r > 0)

    m.stop_loss_trigger_rate = stop_hits / len(trades)
    m.target_1_hit_rate = t1_hits / len(trades)
    m.target_2_hit_rate = t2_hits / len(trades)
    m.win_rate = wins / len(trades)
    m.avg_return = float(np.mean(returns))
    m.avg_holding_period = float(np.mean(holds))
    m.stop_loss_trades = stop_hits
    m.target1_trades = t1_hits
    m.profitable_trades = wins

    # 净值曲线与最大回撤：按交易顺序累计收益
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

    m.evidence = {
        "initial_capital": initial_capital,
        "final_equity": round(equity, 2),
    }
    return m
