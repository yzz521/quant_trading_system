"""手动跑一轮影子模式：生成订单意图并落盘 —— **不发送任何委托**。

用途
----
调度器每轮会自动跑影子模式（见 ``MarketScheduler._shadow_tick``）。本脚本用于
**手动复盘**：跑一次扫描 → 生成订单意图 → 落盘 → 与信号层对账，把结果打出来看。

用法::

    PYTHONPATH=/Users/yzz/workspace/gp .venv/bin/python examples/run_shadow.py
    # 只看对账、不重新扫描（复用已有台账与信号）：
    PYTHONPATH=/Users/yzz/workspace/gp .venv/bin/python examples/run_shadow.py --reconcile-only

需要联网（重新扫描时）。影子模式**没有任何下单能力**，也不会导入任何券商接口。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/yzz/workspace/gp")

from quant_trading_system.stock_analysis.opportunity import OpportunityBatchScanner  # noqa: E402
from quant_trading_system.stock_analysis.paper_tracking import load_signals  # noqa: E402
from quant_trading_system.stock_analysis.portfolio_risk import beijing_date  # noqa: E402
from quant_trading_system.stock_analysis.screener import screen_candidates  # noqa: E402
from quant_trading_system.stock_analysis.sector import get_stock_sectors  # noqa: E402
from quant_trading_system.stock_analysis.shadow_orders import (  # noqa: E402
    build_shadow_orders,
    load_equity_baseline,
    load_shadow_orders,
    reconcile_with_signals,
    record_shadow_orders,
    recordable,
    summarize_shadow,
)

TOP_N = 40


def _equity() -> float | None:
    """账户总资产：优先净值历史，其次持仓成本 + 可用现金。"""
    eq, _prev = load_equity_baseline()
    if eq:
        return float(eq)
    try:
        from quant_trading_system.stock_analysis.holdings import Holdings

        snap = Holdings().capital_snapshot() or {}
        return float(snap.get("total_capital") or 0) or None
    except Exception:
        return None


def _holdings_snapshot():
    """返回 ``(按代码市值, 合计市值, 原始持仓列表)``。"""
    try:
        from quant_trading_system.stock_analysis.holdings import Holdings

        rows = Holdings().all() or []
    except Exception:
        rows = []
    by_code: dict = {}
    total = 0.0
    for h in rows:
        code = str(h.get("code") or "")
        if not code:
            continue
        px = float(h.get("current_price") or h.get("cost_price") or 0)
        val = float(h.get("quantity") or 0) * px
        by_code[code] = by_code.get(code, 0.0) + val
        total += val
    return by_code, total, rows


def main() -> None:
    reconcile_only = "--reconcile-only" in sys.argv
    today = str(beijing_date())

    equity = _equity()
    held_by_code, holdings_value, _rows = _holdings_snapshot()
    print(f"账户总资产：{equity:,.0f} 元" if equity else "账户总资产：未知")
    print(f"当前持仓市值：{holdings_value:,.0f} 元（{len(held_by_code)} 只）")

    if not reconcile_only:
        sector_map = get_stock_sectors() or {}
        cands = screen_candidates("CN", top_n=TOP_N, industry_map=sector_map)
        res = OpportunityBatchScanner(fetch_fundamentals=True, workers=5).scan(cands, market="CN")
        print(f"扫描完成：{len(res.plans)} 个计划")

        existing = [(str(r.get("date")), str(r.get("code")), str(r.get("side")))
                    for r in load_shadow_orders()]
        run = build_shadow_orders(
            res.plans,
            equity=equity,
            holdings_value=holdings_value,
            held_by_code=held_by_code,
            existing_keys=existing,
            market="CN",
            quote_ts=time.time(),      # 本轮数据刚刚取到，新鲜度即此刻
        )
        fresh = record_shadow_orders(recordable(run))
        print(f"新增台账 {len(fresh)} 条\n")
        print(run.summary())

    # ---- 对账：执行层 vs 信号层 ----
    sigs = [s for s in load_signals() if str(s.get("signal_date")) == today]
    rec = reconcile_with_signals(load_shadow_orders(), sigs, date=today)
    print(f"\n=== 对账（{today}）：信号 {rec['n_signals']} 条 / 可下订单 {rec['n_orders']} 条 ===")
    print(f"  口径一致：{'是' if rec['ok'] else '否'}")
    if rec["missing_orders"]:
        print(f"  [真·漏单] 有信号但执行层毫无反应：{rec['missing_orders']}")
    if rec["infeasible"]:
        print(f"  [不可行] 有信号但被明确拒绝（账户规模/风控）：{rec['infeasible']}")
        print("           —— 这不是代码缺陷，是「信号层说能买、执行层说买不起」的结构性矛盾")
    if rec["extra_orders"]:
        print(f"  [凭空下单] 无信号却生成了订单：{rec['extra_orders']}")
    for m in rec["price_mismatch"]:
        print(f"  [价格分叉] {m['code']} 信号 {m['signal_price']} vs 订单 {m['order_price']}")

    print(f"\n=== 台账汇总 ===\n  {summarize_shadow()}")


if __name__ == "__main__":
    main()
