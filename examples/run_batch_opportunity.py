"""批量机会扫描示例 —— 一批候选股 → 交易计划列表（今日机会）。

用法::

    python examples/run_batch_opportunity.py 600000 000001 600519
    python examples/run_batch_opportunity.py --codes "600000,000001" --account 100000

输出按机会分排序的交易计划（过滤 AVOID）。也可配合漏斗关注池使用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.opportunity import (
    OpportunityBatchScanner,
    OpportunityEngine,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="批量机会扫描")
    ap.add_argument("codes", nargs="*", help="股票代码（空格分隔）")
    ap.add_argument("--codes", dest="codes_csv", default="", help="逗号分隔代码")
    ap.add_argument("--account", type=float, default=100_000, help="账户资金（元）")
    ap.add_argument("--workers", type=int, default=5, help="并发数")
    ap.add_argument("--min-score", type=float, default=0.0, help="机会分下限")
    args = ap.parse_args()

    codes = list(args.codes)
    if args.codes_csv:
        codes += [c.strip() for c in args.codes_csv.replace("，", ",").split(",") if c.strip()]
    if not codes:
        print("用法: python examples/run_batch_opportunity.py 600000 000001 [--codes 'a,b']")
        return

    engine = OpportunityEngine(account_equity=args.account, fetch_news=True)
    scanner = OpportunityBatchScanner(
        engine=engine,
        workers=args.workers,
        min_opportunity_score=args.min_score,
    )
    print(f"批量机会扫描 {len(codes)} 只（并发 {args.workers}）...")
    res = scanner.scan(codes, market="CN")

    print(f"\n🎯 今日机会：{len(res.plans)} 个有效计划（耗时 {res.elapsed:.1f}s）")
    for p in res.plans:
        emoji = p.get("decision_emoji", "")
        print(
            f"  {emoji} {p['name']}({p['code']}) {p['decision']} | "
            f"个股{p['stock_score']}/机会{p['opportunity_score']} | "
            f"现价{p['current_price']} | 入场{p['entry_low']}~{p['entry_high']} | "
            f"止损{p['stop_loss']} | 目标{p['target_1']}/{p['target_2']} | "
            f"RR 1:{p['risk_reward_1']} | 仓位{p['position_percent']}%"
        )
    if res.failed:
        print(f"\n⚠️ {len(res.failed)} 只分析失败: {[f['code'] for f in res.failed]}")
    print()


if __name__ == "__main__":
    main()
