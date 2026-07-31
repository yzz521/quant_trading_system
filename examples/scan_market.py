"""全市场选股扫描 — 按技术条件筛选股票池.

Usage::

    python examples/scan_market.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import StockScanner, PRESETS


def main() -> None:
    scanner = StockScanner(max_workers=8)

    # 示例股票池（可换成 scanner.a_share_universe(limit=200) 扫全市场前200只）
    # codes = [
    #     "600000", "000001", "600036", "601318", "000858",
    #     "600519", "601166", "002594", "300750", "000333",
    #     "600276", "000651", "601888", "002475", "300059",
    # ]
    codes = scanner.a_share_universe(limit=200)

    conditions = ["多头排列", "MACD金叉", "突破新高", "放量", "超卖", "触布林下轨"]
    print(f"扫描 {len(codes)} 只标的，条件: {conditions}")
    print("可用预设条件:", list(PRESETS.keys()))
    print("-" * 70)

    hits = scanner.scan(codes, conditions, limit=20)

    if not hits:
        print("今日无标的命中条件（可能处于震荡行情）")
        return

    print(f"命中 {len(hits)} 只：\n")
    print(f"{'代码':<8}{'名称':<8}{'现价':>8}{'涨跌%':>8}{'评分':>6}  匹配条件")
    print("-" * 70)
    for h in hits:
        print(f"{h.code:<8}{h.name:<8}{h.close:>8.2f}{h.change_pct:>+7.2f}%{h.score:>5}  {', '.join(h.matched)}")


if __name__ == "__main__":
    main()
