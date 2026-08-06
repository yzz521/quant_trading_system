"""收盘漏斗冒烟脚本 —— 跑通四层过滤并打印各层数量与 Top N（不发邮件）。

Usage::

    python examples/run_funnel.py                # 全市场跑一遍（约 5-10 分钟）
    python examples/run_funnel.py --limit 200   # 只扫前 200 只候选（快速冒烟）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.funnel import FunnelScanner
from quant_trading_system.utils import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="收盘漏斗冒烟")
    parser.add_argument("--config", default="config/notify.yaml")
    parser.add_argument("--limit", type=int, default=0,
                        help="L1 后最多保留 N 只候选（小规模测试）")
    args = parser.parse_args()

    cfg = (load_yaml(args.config) or {}).get("funnel") or {}
    if args.limit:
        cfg["l1_limit"] = args.limit

    result = FunnelScanner(cfg).run()
    print(f"\n=== 漏斗结果：全市场 {result['total']} 只 ===")
    for s in result["stages"]:
        print(f"  {s['name']}: {s['before']} -> {s['after']}")
    print(f"  耗时 {result['elapsed']}s")
    print("\nTop N 关注池：")
    for h in result["hits"]:
        print(
            f"  {h['code']} {h['name']} | {h['close']} ({h.get('change_pct')}%) | "
            f"评分{h['score']} | 市值{h.get('market_cap')}亿 PE{h.get('pe')} "
            f"换手{h.get('turnover')}% 主力净流入{h.get('main_net')} | "
            f"{'/'.join(h.get('matched') or [])}"
        )


if __name__ == "__main__":
    main()
