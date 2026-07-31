"""我的持仓盈亏 — 查看所有或指定市场的持仓盈亏.

Usage::

    python examples/my_holdings.py           # 全部持仓
    python examples/my_holdings.py CN        # 仅A股
    python examples/my_holdings.py US        # 仅美股
    python examples/my_holdings.py HK        # 仅港股
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import Holdings

DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "holdings.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="查看我的持仓盈亏")
    parser.add_argument("market", nargs="?", default=None, choices=["CN", "US", "HK"])
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="持仓配置文件")
    args = parser.parse_args()

    h = Holdings(args.config)
    if h.is_empty():
        print("暂无持仓，请在 config/holdings.yaml 录入你的持仓")
        return

    positions, summary = h.compute_pnl(args.market)
    mname = {"CN": "A股", "US": "美股", "HK": "港股", None: "全部"}.get(args.market, "全部")

    print(f"\n=== 我的{mname}持仓 ===")
    header = f"{'代码':<8}{'名称':<12}{'持仓':>8}{'成本':>10}{'现价':>10}{'盈亏':>14}{'盈亏%':>10}{'天数':>6}"
    print(header)
    print("-" * len(header.encode("gbk", errors="replace")) if False else "-" * 78)
    for p in positions:
        pnl = p.get("pnl")
        pnl_s = "—" if pnl is None else f"{pnl:+,.2f}"
        pct = p.get("pnl_pct")
        pct_s = "—" if pct is None else f"{pct:+.2f}%"
        print(f"{p['code']:<8}{p['name']:<12}{int(p['quantity']):>8}{p['cost_price']:>10}"
              f"{str(p.get('current_price', '—')):>10}{pnl_s:>14}{pct_s:>10}{p.get('hold_days', 0):>6}")
    print("-" * 78)
    print(f"合计: 持{summary['count']}只 | 成本 {summary['total_cost']:,.2f} | "
          f"市值 {summary['total_value']:,.2f} | "
          f"盈亏 {summary['total_pnl']:+,.2f} ({summary['total_pnl_pct']:+.2f}%)")


if __name__ == "__main__":
    main()
