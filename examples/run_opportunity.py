"""V2 单票交易机会分析示例 —— 从「看指标」到「给计划」。

用法::

    python -m examples.run_opportunity 600000

输出一份 TradingPlan（决策/入场/止损/目标/仓位），即 V2 的核心产物。
联网时用真实日K；离线演示可传 --synthetic。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# 仓库根目录即包目录（quant_trading_system/），包名映射在其父目录
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.market import detect_market_regime
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine


def _synthetic_df() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(42)
    close = 10 + np.cumsum(rng.normal(0.03, 0.12, 160))
    high = close * (1 + np.abs(rng.normal(0, 0.012, 160)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, 160)))
    volume = rng.uniform(1e6, 5e6, 160)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


def main() -> None:
    ap = argparse.ArgumentParser(description="V2 单票交易机会分析")
    ap.add_argument("code", nargs="?", default="600000", help="股票代码")
    ap.add_argument("--account", type=float, default=100_000, help="账户资金（元）")
    ap.add_argument("--synthetic", action="store_true", help="离线演示：使用合成数据")
    args = ap.parse_args()

    if args.synthetic:
        df = _synthetic_df()
        name = "合成示例"
    else:
        info = detect_market(args.code)
        raw = fetch_kline(info, days=250)
        if raw is None or raw.empty:
            print(f"无法获取 {args.code} 行情")
            return
        df = add_all_indicators(raw)
        name = info.code

    # 市场环境（真实上证指数；失败时给出中性状态，不阻塞分析）
    regime = detect_market_regime(None)
    try:
        from quant_trading_system.stock_analysis.market import fetch_market_context

        mkt = fetch_market_context("sh000001")
        if mkt.get("regime") is not None:
            regime = mkt["regime"]
    except Exception:  # noqa: BLE001
        pass

    engine = OpportunityEngine(
        account_equity=args.account,
        regime_score=regime.score,
        market_factor=regime.factor,
        fetch_news=not args.synthetic,
    )
    res = engine.analyze(args.code, name, df)

    if res.plan is None:
        print("数据不足，无法生成交易计划")
        return

    p = res.plan
    print(f"\n📋 {p.name} ({p.code})  交易计划")
    print(f"   决策: {p.decision.emoji} {p.decision.value}")
    print(f"   个股评分: {p.stock_score}   机会评分: {p.opportunity_score}   置信度: {p.confidence}")
    print(f"   现价: {p.current_price}")
    print(f"   入场区间: {p.entry_low} ~ {p.entry_high} (标准 {p.entry_price})")
    print(f"   止损: {p.stop_loss}")
    print(f"   目标: T1 {p.target_1}  T2 {p.target_2}  T3 {p.target_3}")
    print(f"   风险收益: 1:{p.risk_reward_1}  (T2: 1:{p.risk_reward_2})")
    if p.position_percent is not None:
        print(f"   建议仓位: {p.position_percent}%")
    print(f"   持有周期: {p.holding_period}")
    if p.reasons:
        print("\n   理由:")
        for r in p.reasons:
            print(f"     - {r}")
    if p.risks:
        print("\n   风险:")
        for r in p.risks:
            print(f"     - {r}")
    if p.invalidate_condition:
        print(f"\n   ⚠️ 失效条件: {p.invalidate_condition}")
    print()


if __name__ == "__main__":
    main()
