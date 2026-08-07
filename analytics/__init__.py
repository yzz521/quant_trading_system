"""Analytics: performance metrics, plots and an HTML report."""
from .metrics import compute_metrics, compute_trade_stats
from .benchmark import compute_benchmark_metrics, synthetic_benchmark_from_drift
from .plot import plot_equity_drawdown, plot_monthly_returns
from .report import PerformanceReport

__all__ = [
    "compute_metrics",
    "compute_trade_stats",
    "plot_equity_drawdown",
    "plot_monthly_returns",
    "PerformanceReport",
    "compute_benchmark_metrics",
    "synthetic_benchmark_from_drift",
]
