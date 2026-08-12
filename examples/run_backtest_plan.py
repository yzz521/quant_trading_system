"""Trading Plan 历史回测示例 —— 验证入场/止损/目标规则在历史上的有效性。

用法::

    python examples/run_backtest_plan.py 600000 --days 750
    python examples/run_backtest_plan.py --synthetic   # 离线演示

输出样本量/入场命中率/止损触发率/T1T2命中率/胜率/平均收益/最大回撤等指标。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine


def _synthetic_df():
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(5)
    n = 420
    close = 10 + np.cumsum(rng.normal(0.02, 0.15, n))
    high = close * (1 + np.abs(rng.normal(0, 0.015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.015, n)))
    volume = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": volume * close,
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        }
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Trading Plan 历史回测")
    ap.add_argument("code", nargs="?", default="600000")
    ap.add_argument("--days", type=int, default=750, help="回测用K线天数")
    ap.add_argument("--stride", type=int, default=5, help="每隔 N 日生成一个计划")
    ap.add_argument("--account", type=float, default=100_000)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.synthetic:
        raw = _synthetic_df()
        name = "合成示例"
    else:
        info = detect_market(args.code)
        raw = fetch_kline(info, days=args.days)
        if raw is None or raw.empty:
            print(f"无法获取 {args.code} 行情")
            return
        name = info.code
    df = add_all_indicators(raw)

    engine = OpportunityEngine(account_equity=args.account, regime_score=65)
    bt = TradingPlanBacktest(engine=engine, stride=args.stride)
    res = bt.run(df, name, name)

    if res.metrics is None or res.metrics.sample_size == 0:
        print("无有效样本")
        return

    m = res.metrics
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return

    print(f"\n📊 Trading Plan 历史回测: {name}")
    print(f"   样本数(计划数): {m.sample_size}")
    print(f"   入场区命中率:   {m.entry_zone_hit_rate:.1%}")
    print(f"   止损触发率:     {m.stop_loss_trigger_rate:.1%} ({m.stop_loss_trades} 笔)")
    print(f"   目标1命中率:    {m.target_1_hit_rate:.1%} ({m.target1_trades} 笔)")
    print(f"   目标2命中率:    {m.target_2_hit_rate:.1%}")
    print(f"   胜率:           {m.win_rate:.1%} ({m.profitable_trades}/{m.total_trades})")
    print(f"   平均单笔收益:   {m.avg_return:.2f}%")
    print(f"   平均持有:       {m.avg_holding_period:.1f} 交易日")
    print(f"   最大回撤:       {m.max_drawdown:.2f}%")
    print()


if __name__ == "__main__":
    main()
