"""Performance metrics — returns, risk and trade statistics.

All return figures are arithmetic unless noted. The annualization factor
defaults to 252 trading days; pass ``periods_per_year`` to override for
intraday strategies.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from ..portfolio import Portfolio


def _safe_div(a: float, b: float) -> float:
    return a / b if b not in (0, 0.0) else 0.0


def compute_metrics(portfolio: Portfolio, periods_per_year: int = 252,
                    risk_free: float = 0.0) -> dict:
    eq = portfolio.equity_curve_frame()
    if eq.empty:
        return {}
    equity = eq["equity"]
    returns = eq["return"].fillna(0.0)

    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    n = len(returns)
    years = n / periods_per_year if n else 0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    annual_vol = returns.std(ddof=0) * math.sqrt(periods_per_year)
    sharpe = _safe_div(annual_return - risk_free, annual_vol)

    downside = returns[returns < 0].std(ddof=0) * math.sqrt(periods_per_year)
    sortino = _safe_div(annual_return - risk_free, downside)

    cummax = equity.cummax()
    drawdown = equity / cummax - 1.0
    max_dd = float(drawdown.min())
    # longest drawdown duration
    in_dd = drawdown < 0
    dd_durations = []
    start = None
    for i, v in enumerate(in_dd):
        if v and start is None:
            start = i
        elif not v and start is not None:
            dd_durations.append(i - start)
            start = None
    if start is not None:
        dd_durations.append(n - start)
    max_dd_duration = max(dd_durations) if dd_durations else 0

    calmar = _safe_div(annual_return, abs(max_dd)) if max_dd < 0 else 0.0

    # Daily win rate (proxy if no trades)
    win_days = int((returns > 0).sum())
    loss_days = int((returns < 0).sum())
    daily_win_rate = _safe_div(win_days, win_days + loss_days)

    trades = compute_trade_stats(portfolio)

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "max_drawdown_duration": max_dd_duration,
        "calmar": calmar,
        "daily_win_rate": daily_win_rate,
        "n_trading_days": n,
        "final_equity": float(equity.iloc[-1]),
        "initial_capital": float(equity.iloc[0]),
        "n_orders": len(portfolio.fills),
        **trades,
    }


def compute_trade_stats(portfolio: Portfolio) -> dict:
    trades = portfolio.trades
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "total_realized_pnl": portfolio.total_realized_pnl()}
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    win_rate = _safe_div(len(wins), len(pnls))
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = _safe_div(gross_win, gross_loss)
    expectancy = pnls.mean()
    return {
        "n_trades": len(pnls),
        "win_rate": win_rate,
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "profit_factor": profit_factor,
        "expectancy": float(expectancy),
        "total_realized_pnl": float(pnls.sum()),
    }
