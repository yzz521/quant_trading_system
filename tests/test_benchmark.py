import numpy as np
import pandas as pd
from quant_trading_system.analytics import compute_benchmark_metrics, synthetic_benchmark_from_drift
from quant_trading_system.portfolio import Portfolio
from quant_trading_system.core import Direction, FillEvent
from datetime import datetime

def test_benchmark_metrics_smoke():
    pf = Portfolio(100_000, t1_enabled=False)
    # build equity curve manually
    idx = pd.date_range("2024-01-01", periods=50, freq="B")
    eq = 100_000 * (1 + np.linspace(0, 0.1, 50))
    pf.equity_curve = list(zip(idx.to_pydatetime(), eq))
    bm = synthetic_benchmark_from_drift(idx, annual_drift=0.05, seed=2)
    m = compute_benchmark_metrics(pf, bm)
    assert "information_ratio" in m
    assert "excess_total_return" in m
    assert m["n_overlap_days"] == 50
