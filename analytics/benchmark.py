"""Benchmark-relative performance (excess return, information ratio, beta)."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from ..portfolio import Portfolio


def _safe_div(a: float, b: float) -> float:
    return a / b if b not in (0, 0.0) and not (isinstance(b, float) and math.isnan(b)) else 0.0


def align_returns(
    portfolio: Portfolio,
    benchmark: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Align strategy daily returns with benchmark price or return series."""
    eq = portfolio.equity_curve_frame()
    if eq.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    strat = eq["return"].fillna(0.0)
    strat.index = pd.to_datetime(strat.index)

    bm = benchmark.copy()
    bm.index = pd.to_datetime(bm.index)
    # If looks like price level (mostly positive, not centered at 0), convert to returns
    if bm.abs().median() > 0.2:  # heuristic: prices vs daily returns
        bm = bm.sort_index().pct_change().fillna(0.0)
    else:
        bm = bm.fillna(0.0)

    joined = pd.concat([strat.rename("strat"), bm.rename("bm")], axis=1, join="inner").dropna()
    return joined["strat"], joined["bm"]


def compute_benchmark_metrics(
    portfolio: Portfolio,
    benchmark: pd.Series,
    periods_per_year: int = 252,
    risk_free: float = 0.0,
) -> dict:
    """Compare portfolio equity curve to a benchmark series (prices or returns).

    Returns keys: excess_total_return, excess_annual, tracking_error, information_ratio,
    beta, correlation, benchmark_total_return, strategy_total_return.
    """
    strat_r, bm_r = align_returns(portfolio, benchmark)
    if strat_r.empty or len(strat_r) < 2:
        return {}

    strat_total = float((1.0 + strat_r).prod() - 1.0)
    bm_total = float((1.0 + bm_r).prod() - 1.0)
    excess_total = strat_total - bm_total

    n = len(strat_r)
    years = n / periods_per_year if n else 0
    strat_ann = (1.0 + strat_total) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    bm_ann = (1.0 + bm_total) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    excess_ann = strat_ann - bm_ann

    active = strat_r - bm_r
    te = float(active.std(ddof=0) * math.sqrt(periods_per_year))
    ir = _safe_div(excess_ann, te)

    # beta via covariance
    cov = float(np.cov(strat_r.to_numpy(), bm_r.to_numpy(), ddof=0)[0, 1])
    var_bm = float(np.var(bm_r.to_numpy()))
    beta = _safe_div(cov, var_bm)
    corr = float(strat_r.corr(bm_r)) if strat_r.std() > 0 and bm_r.std() > 0 else 0.0

    return {
        "strategy_total_return": strat_total,
        "benchmark_total_return": bm_total,
        "excess_total_return": excess_total,
        "strategy_annual_return": strat_ann,
        "benchmark_annual_return": bm_ann,
        "excess_annual_return": excess_ann,
        "tracking_error": te,
        "information_ratio": ir,
        "beta": beta,
        "correlation": corr,
        "n_overlap_days": n,
    }


def synthetic_benchmark_from_drift(
    index: pd.DatetimeIndex,
    annual_drift: float = 0.08,
    annual_vol: float = 0.15,
    seed: int = 0,
    start_price: float = 1000.0,
) -> pd.Series:
    """Build a synthetic index price series aligned to ``index`` (for offline demos)."""
    rng = np.random.default_rng(seed)
    n = len(index)
    if n == 0:
        return pd.Series(dtype=float)
    daily_mu = annual_drift / 252.0
    daily_sig = annual_vol / math.sqrt(252.0)
    rets = rng.normal(daily_mu, daily_sig, size=n)
    prices = start_price * np.cumprod(1.0 + rets)
    return pd.Series(prices, index=pd.to_datetime(index), name="benchmark")
