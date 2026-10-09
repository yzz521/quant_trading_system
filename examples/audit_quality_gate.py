"""质量闸门全市场验证 —— 「完整财务数据下，闸门到底放行多少」。

背景
----
质量闸门（``stock_analysis.opportunity.quality_gate``）上线后有一个悬而未决的问题：
**在拿到完整财务数据时，它的通过率是多少？** 太严（几乎不放行）等于系统停摆，
太松（几乎全放行）等于没把关。必须用一轮真实全市场扫描把通过率量出来。

本脚本做三件事：
  1. 记录本轮**是否真的**取到财务数据（``fundamentals_available``），
     以及闸门口径是否被静默放宽（``gate_note``）；
  2. 对全市场初筛候选跑机会引擎，统计决策分布与闸门三档分布；
  3. 输出每条规则的拦截次数（``checks``），定位「到底是谁在拦」。

用法::

    python examples/audit_quality_gate.py --top-n 40
    python examples/audit_quality_gate.py --top-n 40 --json results/quality_gate_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.opportunity import (  # noqa: E402
    OpportunityBatchScanner,
    OpportunityEngine,
    QualityGateConfig,
)
from quant_trading_system.stock_analysis.screener import screen_candidates  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _industry_map() -> dict:
    """行业映射（用于候选池行业分层 + 组合风控）；取不到就返回空。"""
    try:
        from quant_trading_system.stock_analysis.sector import get_stock_sectors

        m = get_stock_sectors()
        return m if isinstance(m, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 行业映射不可用（{e}）—— 候选池不做行业分层")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="质量闸门全市场验证")
    ap.add_argument("--top-n", type=int, default=40, help="初筛候选数")
    ap.add_argument("--workers", type=int, default=5, help="并发数")
    ap.add_argument("--account", type=float, default=100_000, help="账户资金")
    ap.add_argument("--json", default="", help="结果落盘路径")
    args = ap.parse_args()

    print("=" * 78)
    print("质量闸门全市场验证")
    print("=" * 78)

    imap = _industry_map()
    cands = screen_candidates("CN", args.top_n, industry_map=imap)
    print(f"\n初筛候选：{len(cands)} 只（行业映射 {len(imap)} 条）")
    if not cands:
        print("初筛无候选 —— 数据源不可用，无法验证。")
        return 1
    with_pe = sum(1 for c in cands if c.get("pe") is not None)
    with_cap = sum(1 for c in cands if c.get("total_cap_yi") is not None)
    with_to = sum(1 for c in cands if c.get("turnover") is not None)
    print(f"  候选自带字段：PE {with_pe}/{len(cands)}  "
          f"总市值 {with_cap}/{len(cands)}  换手 {with_to}/{len(cands)}")

    gate = QualityGateConfig()
    engine = OpportunityEngine(account_equity=args.account, fetch_news=False)
    scanner = OpportunityBatchScanner(
        engine=engine, workers=args.workers, include_avoid=True,
        fetch_fundamentals=True, gate=gate,
    )
    print(f"\n扫描中（并发 {args.workers}，闸门门槛：质量分≥{gate.min_stock_score} "
          f"机会分≥{gate.min_opportunity_score} RR≥{gate.min_rr} "
          f"覆盖率≥{gate.min_data_coverage:.0%}）…")
    res = scanner.scan(cands, market="CN")

    # ---- 对照：同一批候选、闸门关闭 → 量化「闸门到底拦了多少」----
    print("对照扫描中（闸门关闭）…")
    scanner_off = OpportunityBatchScanner(
        engine=OpportunityEngine(account_equity=args.account, fetch_news=False),
        workers=args.workers, include_avoid=True, fetch_fundamentals=True,
        gate=QualityGateConfig.disabled(),
    )
    res_off = scanner_off.scan(cands, market="CN")
    dec_off = Counter()
    for it in (res_off.items or []):
        d = (it.plan or {}).get("decision")
        dec_off[str(getattr(d, "value", d))] += 1

    print(f"\n完成，耗时 {res.elapsed:.1f}s（对照 {res_off.elapsed:.1f}s）")
    print(f"本轮是否真取到财务数据：{'是' if res.fundamentals_available else '否'}")
    if res.gate_note:
        print(f"闸门口径说明：{res.gate_note}")

    # ---- 决策分布 ----
    items = res.items or []
    dec = Counter()
    tier = Counter()
    fails = Counter()
    cov_ratios = []
    downgrades = Counter()
    buy_now_blocked_by_gate = 0
    for it in items:
        p = it.plan or {}
        d = p.get("decision")
        dec[str(getattr(d, "value", d))] += 1
        meta = p.get("meta") or {}
        gd = meta.get("gate_downgrade") or ""
        if gd:
            downgrades[str(gd)] += 1
            if "BUY_NOW" in str(gd):
                buy_now_blocked_by_gate += 1
        g = meta.get("quality_gate") or {}
        if g:
            tier[str(g.get("tier"))] += 1
            for r in (g.get("failed") or []):
                fails[str(r).split("（")[0].split("(")[0].strip()] += 1
            cov = g.get("coverage") or {}
            if cov.get("ratio") is not None:
                cov_ratios.append(float(cov["ratio"]))
        elif it.error:
            tier["<分析失败>"] += 1

    n_ok = sum(dec.values())
    n_buy_now = dec.get("BUY_NOW", 0)
    n_pullback = dec.get("BUY_ON_PULLBACK", 0)
    n_valid = sum(v for k, v in tier.items() if k != "<分析失败>")
    n_gate_buy = tier.get("BUY", 0)

    print("\n决策分布（价格位置决定的执行档）")
    print("-" * 78)
    print(f"  {'决策':<18}{'闸门开':>8}{'闸门关':>8}{'差额':>8}")
    for k in sorted(set(dec) | set(dec_off), key=lambda x: -(dec.get(x, 0))):
        a, b = dec.get(k, 0), dec_off.get(k, 0)
        print(f"  {k:<18}{a:>8}{b:>8}{a - b:>+8}")
    print(f"  {'分析失败':<18}{len(res.failed):>8}{len(res_off.failed):>8}")

    print("\n质量闸门三档分布（闸门本身的判定）")
    print("-" * 78)
    for k, v in tier.most_common():
        print(f"  {k:<18}{v:>5}")

    # ---- 分数分布：判断门槛卡在什么分位上 ----
    def _pct(vals, p):
        if not vals:
            return None
        s = sorted(vals)
        i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
        return s[i]

    ss = [float((it.plan or {}).get("stock_score")) for it in items
          if (it.plan or {}).get("stock_score") is not None]
    os_ = [float((it.plan or {}).get("opportunity_score")) for it in items
           if (it.plan or {}).get("opportunity_score") is not None]
    rr = [float((it.plan or {}).get("risk_reward_1")) for it in items
          if (it.plan or {}).get("risk_reward_1") is not None]
    print("\n分数分布（定位门槛卡在什么分位）")
    print("-" * 78)
    print(f"  {'指标':<18}{'p10':>8}{'p50':>8}{'p90':>8}{'门槛':>8}")
    for name, vals, thr in (("个股质量分", ss, gate.min_stock_score),
                            ("机会分", os_, gate.min_opportunity_score),
                            ("风险收益比", rr, gate.min_rr)):
        if vals:
            print(f"  {name:<18}{_pct(vals,0.1):>8.1f}{_pct(vals,0.5):>8.1f}"
                  f"{_pct(vals,0.9):>8.1f}{thr:>8.1f}")
    score_stats = {
        "stock_score": {"p10": _pct(ss, .1), "p50": _pct(ss, .5), "p90": _pct(ss, .9),
                        "n": len(ss), "threshold": gate.min_stock_score},
        "opportunity_score": {"p10": _pct(os_, .1), "p50": _pct(os_, .5),
                              "p90": _pct(os_, .9), "n": len(os_),
                              "threshold": gate.min_opportunity_score},
        "risk_reward_1": {"p10": _pct(rr, .1), "p50": _pct(rr, .5),
                          "p90": _pct(rr, .9), "n": len(rr),
                          "threshold": gate.min_rr},
    }

    if cov_ratios:
        print(f"\n关键数据覆盖率：均值 {sum(cov_ratios)/len(cov_ratios):.1%}  "
              f"最小 {min(cov_ratios):.1%}  最大 {max(cov_ratios):.1%}")

    if downgrades:
        print("\n被闸门降级的计划")
        print("-" * 78)
        for k, v in downgrades.most_common():
            print(f"  {k:<48}{v:>5}")

    if fails:
        print("\n拦截原因排行（按规则）")
        print("-" * 78)
        for k, v in fails.most_common(12):
            print(f"  {k:<40}{v:>5}")

    # ---- 结论 ----
    # 关键区分：**闸门通过率**（tier=BUY）衡量「闸门严不严」；
    # **BUY_NOW 通过率**是闸门 + 价格位置两个条件叠加的结果 —— 现价不在入场
    # 区间时即使闸门全过也只会是 BUY_ON_PULLBACK（等回踩），这**不是闸门的问题**。
    print("\n结论")
    print("-" * 78)
    gate_rate = n_gate_buy / max(1, n_valid)
    print(f"  质量闸门通过率（tier=BUY） = {n_gate_buy}/{n_valid} = {gate_rate:.1%}"
          f"   ← 衡量闸门严不严的主指标")
    print(f"  BUY_NOW 通过率（闸门 + 现价已在入场区间） = {n_buy_now}/{n_ok} "
          f"= {n_buy_now / max(1, n_ok):.1%}")
    print(f"  可买入合计（BUY_NOW + BUY_ON_PULLBACK）= {n_buy_now + n_pullback}"
          f"（{(n_buy_now + n_pullback) / max(1, n_ok):.1%}）")
    print(f"  闸门关闭时 BUY_NOW = {dec_off.get('BUY_NOW', 0)} → 闸门拦下 "
          f"{dec_off.get('BUY_NOW', 0) - n_buy_now} 个")
    print(f"  因闸门未过而被降级的计划：{sum(downgrades.values())} 个"
          f"（其中原为 BUY_NOW 的 {buy_now_blocked_by_gate} 个）")
    if n_valid == 0:
        print("  ⚠️ 无有效样本，无法判定。")
    elif gate_rate == 0:
        print("  ⚠️ 畸零：闸门一只都不放行 → 系统停摆风险。")
    elif gate_rate < 0.02:
        print("  ⚠️ 偏低（<2%）：闸门偏严，需确认是否因数据缺失被误拦。")
    elif gate_rate > 0.60:
        print("  ⚠️ 畸高（>60%）：闸门偏松，几乎不构成筛选。")
    else:
        print("  ✓ 闸门通过率落在合理区间（2%~60%），既不畸零也不畸高。")
    if n_buy_now == 0:
        if buy_now_blocked_by_gate > 0:
            print(f"  说明：BUY_NOW=0 的直接原因是**闸门**——{buy_now_blocked_by_gate} 个"
                  f"价格位置已达标（闸门关闭时本会是 BUY_NOW）的计划被降级为 WATCH。")
        elif n_pullback > 0:
            print("  说明：BUY_NOW=0 而 BUY_ON_PULLBACK>0 → 原因是**现价高于入场区间**"
                  "（盘中价格位置），不是闸门拦死。")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "全市场初筛 + 机会引擎（实时行情）",
        "top_n": args.top_n,
        "n_candidates": len(cands),
        "n_analyzed": n_ok,
        "n_valid_gate": n_valid,
        "n_failed": len(res.failed),
        "fundamentals_available": res.fundamentals_available,
        "gate_note": res.gate_note,
        "gate_config": {
            "min_stock_score": gate.min_stock_score,
            "min_opportunity_score": gate.min_opportunity_score,
            "min_rr": gate.min_rr,
            "min_data_coverage": gate.min_data_coverage,
            "require_quality_data": gate.require_quality_data,
        },
        "decisions": dict(dec),
        "decisions_gate_disabled": dict(dec_off),
        "buy_now_blocked_by_gate": (dec_off.get("BUY_NOW", 0) - n_buy_now),
        "gate_tiers": dict(tier),
        "gate_downgrades": dict(downgrades),
        "buy_now_blocked_by_gate": buy_now_blocked_by_gate,
        "blocked_reasons": dict(fails),
        "coverage": {
            "mean": round(sum(cov_ratios) / len(cov_ratios), 3) if cov_ratios else None,
            "min": round(min(cov_ratios), 3) if cov_ratios else None,
            "max": round(max(cov_ratios), 3) if cov_ratios else None,
        },
        "gate_pass_rate": round(gate_rate, 4),
        "buy_now_rate": round(n_buy_now / max(1, n_ok), 4),
        "actionable_rate": round((n_buy_now + n_pullback) / max(1, n_ok), 4),
        "score_stats": score_stats,
        "elapsed": round(res.elapsed, 2),
        "weights_meta": scanner.weights_meta,
    }
    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
