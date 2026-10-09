"""降敞口测算 —— 把「超限」翻译成「卖哪只、卖几股、卖多少钱」。

背景
----
组合风控（``stock_analysis.portfolio_risk``）会报出「单票超限 / 前三大超限 /
行业超限」，但只说「降到 X% 以下」，不说**具体卖多少**。小账户尤其需要具体数字：
A 股最小交易单位 1 手 = 100 股，而账户净值只有约 1 万元 —— 「降到 20%」到底是
卖 20 股还是清仓，必须算出来。

权重口径（重要）
----------------
本脚本与实盘 ``holdings_quant.portfolio_risk_block`` **同一把尺子**：
权重 = 个股市值 ÷ **总资产（含现金）**。
⚠️ 直接调 ``assess_portfolio_risk`` 而不显式传 ``weight`` 时，``holdings_frame``
会退化为「占总持仓」归一化 —— 账户有现金时这会**高估**集中度（本例 52.8% vs 真实
40.5%）。本脚本显式传权重，避免踩这个坑。

本脚本只做**算术**，不构成投资建议。

用法::

    python examples/calc_exposure_reduction.py
    python examples/calc_exposure_reduction.py --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import portfolio_risk as pr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HOLDINGS = ROOT / "results" / "holdings_quant.json"
EQUITY = ROOT / "results" / "equity_history.json"

# 行业归属（A 股银行 / 港股通非银 ETF）。同花顺行业接口需联网，这里对**当前持仓**
# 显式标注，避免行业缺失被归入「未知」而漏报行业超限。
INDUSTRY = {
    "601398": "银行",
    "600000": "银行",
    "600036": "银行",
    "513750": "非银金融",
}
FINANCIAL = {"银行", "非银金融"}      # 合并口径：金融业（同一风险因子）


def _load_holdings() -> tuple[list[dict], float]:
    raw = json.loads(HOLDINGS.read_text(encoding="utf-8"))
    block = raw.get("CN") or next(iter(raw.values()))
    items = block.get("items") or []
    eq_records = json.loads(EQUITY.read_text(encoding="utf-8"))
    equity = float(eq_records[-1]["equity"]) if eq_records else 0.0
    out = []
    for it in items:
        qty = float(it.get("quantity") or 0)
        px = float(it.get("current_price") or it.get("cost_price") or 0)
        code = str(it.get("code"))
        out.append({
            "code": code,
            "name": it.get("name") or code,
            "quantity": qty,
            "price": px,
            "cost_price": float(it.get("cost_price") or 0),
            "market_value": qty * px,
            "industry": INDUSTRY.get(code, "未知"),
            "as_of": it.get("as_of"),
        })
    return out, equity


def _shares_for_amount(price: float, amount: float) -> int:
    """要把市值减掉 ``amount`` 元，至少卖多少股（向上取整）。"""
    if price <= 0:
        return 0
    return max(0, int(math.ceil(amount / price - 1e-9)))


def main() -> int:
    ap = argparse.ArgumentParser(description="降敞口测算")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    holds, equity = _load_holdings()
    if not holds or equity <= 0:
        print("无持仓或无净值数据。")
        return 1

    # 与实盘同一口径：权重 = 市值 / 总资产（含现金）
    payload = [dict(h, weight=h["market_value"] / equity) for h in holds]
    lim = pr.RiskLimits()
    rep = pr.assess_portfolio_risk(payload, limits=lim)

    total_mv = sum(h["market_value"] for h in holds)
    cash = equity - total_mv
    rows = sorted((dict(h, weight=h["market_value"] / equity) for h in holds),
                  key=lambda r: -r["market_value"])
    by_code = {h["code"]: h for h in holds}
    top1 = rows[0]
    top3 = sum(r["market_value"] for r in rows[:3])
    fin_mv = sum(h["market_value"] for h in holds if h["industry"] in FINANCIAL)

    # 逐行业暴露（用风控模块自己的口径，取超限行业）
    frame = pr.holdings_frame(payload)
    ind = pr.industry_exposure(frame, lim)
    over = ind.loc[ind["over_limit"]] if not ind.empty else ind
    worst_ind = None
    if not over.empty:
        worst_ind = over.sort_values("weight", ascending=False).iloc[0]

    targets = []
    # ---- 限值 1：单票 ----
    cap1 = lim.max_single_pct * equity
    need1 = max(0.0, top1["market_value"] - cap1)
    targets.append({
        "limit": f"单票 ≤ {lim.max_single_pct:.0%}", "current": top1["weight"],
        "who": f"{top1['name']}({top1['code']})", "need_amount": need1,
        "need_shares": _shares_for_amount(top1["price"], need1),
        "price": top1["price"], "breach": need1 > 1e-9,
    })
    # ---- 限值 2：前三大 ----
    cap3 = lim.max_top3_pct * equity
    need3 = max(0.0, top3 - cap3)
    targets.append({
        "limit": f"前三大 ≤ {lim.max_top3_pct:.0%}", "current": top3 / equity,
        "who": " / ".join(r["name"] for r in rows[:3]), "need_amount": need3,
        "need_shares": None, "price": None, "breach": need3 > 1e-9,
    })
    # ---- 限值 3：单一行业（取超限行业）----
    if worst_ind is not None:
        capi = lim.max_industry_pct * equity
        needi = max(0.0, float(worst_ind["weight"]) * equity - capi)
        targets.append({
            "limit": f"{worst_ind['industry']} ≤ {lim.max_industry_pct:.0%}",
            "current": float(worst_ind["weight"]), "who": str(worst_ind["industry"]),
            "need_amount": needi, "need_shares": None, "price": None,
            "breach": needi > 1e-9,
        })

    # ---- 方案 A：只把超限单票压回限值内 ----
    sell_a = targets[0]["need_shares"]
    after_a_mv = dict((h["code"], h["market_value"]) for h in holds)
    after_a_mv[top1["code"]] -= sell_a * top1["price"]
    a_top3 = sum(sorted(after_a_mv.values(), reverse=True)[:3])
    a_fin = fin_mv - (sell_a * top1["price"] if top1["industry"] in FINANCIAL else 0)

    # ---- 方案 B：在 A 基础上继续压超限行业至限值内 ----
    extra = []
    unmet = 0.0
    ind_after: dict[str, float] = {}
    if worst_ind is not None:
        target_ind = str(worst_ind["industry"])
        # 该行业当前（减仓 A 后）市值
        for h in holds:
            v = after_a_mv[h["code"]]
            ind_after[h["industry"]] = ind_after.get(h["industry"], 0.0) + v
        capi = lim.max_industry_pct * equity
        remain = max(0.0, ind_after.get(target_ind, 0.0) - capi)
        pool = sorted([h for h in holds if h["industry"] == target_ind
                       and h["code"] != top1["code"]], key=lambda x: -x["market_value"])
        for h in pool:
            if remain <= 1e-9:
                break
            sh = _shares_for_amount(h["price"], min(remain, after_a_mv[h["code"]]))
            sh = min(sh, int(h["quantity"]))
            extra.append({"code": h["code"], "name": h["name"], "shares": sh,
                          "price": h["price"], "amount": sh * h["price"]})
            remain -= sh * h["price"]
        unmet = max(0.0, remain)

    b_fin = a_fin - sum(s["amount"] for s in extra)

    result = {
        "as_of": holds[0].get("as_of"), "equity": equity,
        "total_market_value": total_mv, "cash": cash, "cash_ratio": cash / equity,
        "limits": lim.to_dict(), "positions": rows,
        "risk_report": rep.to_dict(),
        "financial_weight": fin_mv / equity,
        "bank_weight": ind_after.get("银行", 0.0) / equity if worst_ind is not None else None,
        "targets": targets,
        "plan_min": {
            "desc": "方案 A：只把超限的单票压回限值内（通常顺带满足「前三大」）",
            "sell": [{"code": top1["code"], "name": top1["name"], "shares": sell_a,
                      "price": top1["price"], "amount": sell_a * top1["price"]}],
            "after": {"top1_weight": after_a_mv[top1["code"]] / equity,
                      "top3_weight": a_top3 / equity,
                      "financial_weight": a_fin / equity},
        },
        "plan_full": {
            "desc": "方案 B：在方案 A 基础上继续压超限行业敞口至限值内",
            "extra_sell": extra, "unmet_amount": unmet,
            "after": {"top1_weight": after_a_mv[top1["code"]] / equity,
                      "top3_weight": a_top3 / equity,
                      "financial_weight": b_fin / equity},
        },
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    print("=" * 80)
    print(f"降敞口测算   持仓快照 {result['as_of']}   权重口径=市值/总资产(含现金)")
    print("=" * 80)
    print(f"账户净值 ¥{equity:,.2f}   持仓市值 ¥{total_mv:,.2f}   "
          f"现金 ¥{cash:,.2f}（{cash/equity:.1%}）")
    print()
    print(f"{'标的':<22}{'股数':>7}{'现价':>9}{'市值':>11}{'权重':>8}  {'行业':<8}")
    print("-" * 80)
    for r in rows:
        print(f"{r['name']+'('+r['code']+')':<22}{r['quantity']:>7.0f}"
              f"{r['price']:>9.3f}{r['market_value']:>11,.0f}"
              f"{r['weight']:>8.1%}  {r['industry']:<8}")
    print()
    print(f"前三大合计 {top3/equity:.1%}   金融业合计 {fin_mv/equity:.1%}")
    print()
    print("当前超限项（与实盘风控同一套代码、同一权重口径）")
    print("-" * 80)
    if rep.breaches:
        for b in rep.breaches:
            print("  ✗ " + b)
    else:
        print("  无")
    print()
    print("各限值需要减掉多少")
    print("-" * 80)
    print(f"{'限值':<22}{'当前':>8}{'需减(元)':>13}{'≈股数':>8}   对象")
    for t in targets:
        sh = "" if t["need_shares"] is None else f"{t['need_shares']}"
        print(f"{t['limit']:<22}{t['current']:>8.1%}{t['need_amount']:>13,.0f}"
              f"{sh:>8}   {t['who']}  [{'超限' if t['breach'] else '达标'}]")
    print()
    print("方案 A —— 最小合规")
    print("-" * 80)
    s = result["plan_min"]["sell"][0]
    print(f"  卖出 {s['name']}({s['code']}) {s['shares']} 股 @ {s['price']:.3f}"
          f" ≈ ¥{s['amount']:,.0f}")
    a = result["plan_min"]["after"]
    print(f"  减后：单票 {a['top1_weight']:.1%}  前三大 {a['top3_weight']:.1%}  "
          f"金融 {a['financial_weight']:.1%}")
    print()
    print("方案 B —— 完全合规（继续压超限行业敞口）")
    print("-" * 80)
    for s in result["plan_full"]["extra_sell"]:
        print(f"  卖出 {s['name']}({s['code']}) {s['shares']} 股 @ {s['price']:.3f}"
              f" ≈ ¥{s['amount']:,.0f}")
    if not result["plan_full"]["extra_sell"]:
        print("  （无需额外减仓）")
    b = result["plan_full"]["after"]
    print(f"  减后：单票 {b['top1_weight']:.1%}  前三大 {b['top3_weight']:.1%}  "
          f"金融 {b['financial_weight']:.1%}")
    if unmet > 1e-9:
        print(f"  ⚠️ 仍有 ¥{unmet:,.0f} 敞口无法靠卖出消化（该行业持仓已清空）"
              f"—— 只能靠买入其他行业标的摊薄")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
