"""扫描/持仓 → 股票池 → 合成回测（离线演示）。

真实行情可把 SyntheticDataSource 换成 AkShareSource。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.analytics import compute_metrics
from quant_trading_system.backtest import BacktestConfig, BacktestEngine
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.stock_analysis.universe import make_universe
from quant_trading_system.strategy import create_strategy


def main() -> None:
    # 模拟扫描命中
    fake_hits = [{"code": "DEMO_A"}, {"code": "DEMO_B"}]
    universe = make_universe(scan_hits=fake_hits, extra=["DEMO_C"], limit=10)
    print("universe:", universe)

    ds = SyntheticDataSource(seed=11)
    data = {s: ds.get_history(s, "2023-01-01", "2023-12-31") for s in universe}
    feed = BarFeed(data, calendar_market="CN")
    cfg = BacktestConfig(
        t1_enabled=False, enforce_limit=False, enforce_volume=False, lot_size=1
    )
    eng = BacktestEngine(cfg)
    eng.add_strategy(create_strategy("ma_cross", symbols=universe, fast=5, slow=20))
    pf = eng.run(feed)
    m = compute_metrics(pf)
    print({k: m.get(k) for k in ("total_return", "sharpe", "max_drawdown", "n_orders")})


if __name__ == "__main__":
    main()
