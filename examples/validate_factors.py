"""因子验证：用历史回测回答「这些打分因子到底有没有用」。

要解决的问题
------------
打分链路上有 9 个个股分因子、7 个机会分因子、组合分与标定后置信度，但没有人回验
过它们的区分力。经验上「权重拍脑袋 + 从不回验」是量化系统最常见的失效来源：某个
因子长期 IC≈0 甚至反向，却因为权重固定而持续污染排序，表现为「推荐的票怪怪的」。

本脚本产出
----------
1. 每个因子的 IC / IR / t 值 / IC>0 占比 —— 有没有区分力
2. 分位（五分组）平均收益与单调性 —— 能不能按它排序选票
3. 滚动前瞻样本外（训练段早于测试段）—— 结论是不是只在样本内成立
4. 按决策类型分层 IC —— 是不是只在某一类信号里有效
5. 结论落盘 ``results/factor_validation_report.json``，可追溯

用法
----
    # 真实数据（联网拉多只 A 股）
    python examples/validate_factors.py --codes 600519 000001 601398 000858 002594 601857

    # 合成数据（离线，可入 CI）
    python examples/validate_factors.py --synthetic

⚠️ 已知偏差：stride 小于持有期时同一段行情被反复计入，样本重叠会使 IC 的 t 值与
显著性被高估。脚本会在 stride 过小时告警；IC 均值的**方向与量级**仍可用，但不要
只看 t 值下结论。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine
from quant_trading_system.stock_analysis.research import (
    build_factor_panel,
    stratified_ic,
    summarize,
    validate_factor,
    validate_factors,
    walk_forward_factor,
)

DEFAULT_CODES = ["600519", "000001", "601398", "000858", "002594", "601857"]
_FACTORS = ["confidence", "opportunity_score", "stock_score", "risk_reward_1"]


# --------------------------------------------------------------------------- #
def _synthetic_data(codes: list[str], days: int) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes):
        rng = np.random.default_rng(1000 + i)
        drift = rng.normal(0.0004, 0.0006)
        vol = rng.uniform(0.012, 0.028)
        ret = rng.normal(drift, vol, days)
        close = 10.0 * np.exp(np.cumsum(ret))
        intraday = np.abs(rng.normal(0, vol / 2, days))
        volume = rng.uniform(1e6, 5e6, days)
        out[code] = pd.DataFrame({
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close * (1 + intraday),
            "low": close * (1 - intraday),
            "close": close,
            "volume": volume,
            "amount": volume * close,
            "date": pd.date_range("2023-01-02", periods=days, freq="B"),
        })
    return out


def _fetch_real(codes: list[str], days: int) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for code in codes:
        try:
            # fetch_kline 收的是 MarketInfo（detect_market 的返回值），没有 market 关键字。
            # 曾误写成 fetch_kline(code, market=..., days=...) → 每只都 TypeError 被吞掉，
            # 于是「真实数据」路径恒为空、报告永远只能跑合成数据。
            info = detect_market(code)
            df = fetch_kline(info, days=days)
        except Exception as exc:                       # noqa: BLE001
            print(f"  {code}: 拉取失败（{exc}）")
            continue
        if df is None or len(df) < 120:
            print(f"  {code}: 数据不足（{0 if df is None else len(df)} 根K线）")
            continue
        out[code] = df
    return out


def _collect_trades(data: dict, *, min_rr: float, stride: int,
                    max_hold: int, account: float) -> list:
    trades: list = []
    for code, raw in data.items():
        df = add_all_indicators(raw)
        engine = OpportunityEngine(account_equity=account, regime_score=65)
        bt = TradingPlanBacktest(engine=engine, min_rr=min_rr,
                                 max_hold_days=max_hold, stride=stride)
        res = bt.run(df, code, code)
        n = len(res.trades or [])
        print(f"  {code}: {n} 笔计划" if n else f"  {code}: 无有效样本")
        trades.extend(res.trades or [])
    return trades


def _print_table(df: pd.DataFrame) -> None:
    if df.empty:
        print("（无可用因子）")
        return
    print(f"{'因子':<20} {'样本':>6} {'期数':>5} {'IC均值':>8} {'IR':>7} "
          f"{'t值':>7} {'IC>0':>6} {'顶底差':>8} {'单调':>5}  结论")
    print("-" * 108)
    for _, r in df.iterrows():
        def _f(x, w=8, p=3):
            return f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) and np.isfinite(x) else f"{'-':>{w}}"
        print(f"{r['factor']:<20} {r['n_obs']:>6} {r['n_periods']:>5} "
              f"{_f(r['ic_mean'])} {_f(r['ir'], 7)} {_f(r['t_stat'], 7)} "
              f"{_f(r['ic_win_rate'], 6, 2)} {_f(r['top_minus_bottom'])} "
              f"{('是' if r['monotone'] else '否'):>5}  {r['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="因子验证：IC/IR、分组单调性、滚动前瞻样本外")
    ap.add_argument("--codes", nargs="*", default=[], help="股票代码列表")
    ap.add_argument("--synthetic", action="store_true", help="用合成数据（离线）")
    ap.add_argument("--days", type=int, default=900, help="每只股票K线天数")
    ap.add_argument("--stride", type=int, default=5, help="每隔 N 日生成一个计划")
    ap.add_argument("--min-rr", type=float, default=1.5, help="只统计 RR ≥ 该值的计划")
    ap.add_argument("--max-hold", type=int, default=60, help="最长持有交易日")
    ap.add_argument("--quantiles", type=int, default=5, help="分位组数")
    ap.add_argument("--splits", type=int, default=5, help="滚动前瞻折数")
    ap.add_argument("--account", type=float, default=100_000)
    ap.add_argument("--out", default="", help="报告输出路径（默认 results/factor_validation_report.json）")
    args = ap.parse_args()

    codes = args.codes or DEFAULT_CODES
    print("=" * 66)
    print("因子验证")
    print("=" * 66)

    if args.synthetic:
        source = "合成数据(离线)"
        data = _synthetic_data(codes, args.days)
    else:
        source = "实时行情"
        data = _fetch_real(codes, args.days)
    if not data:
        print("无可用数据。")
        return 1
    print(f"\n数据源：{source}（{len(data)} 只）")
    print(f"回测参数：stride={args.stride} min_rr={args.min_rr} max_hold={args.max_hold}")

    if args.stride < args.max_hold / 2:
        print(f"  ⚠️ stride={args.stride} 明显小于持有期 {args.max_hold}，回测样本高度重叠、"
              f"互相不独立，\n     IC 的 t 值与显著性会被高估。方向与量级仍可用，"
              f"但不要只看 t 值下结论。")

    print("\n回测中…")
    trades = _collect_trades(data, min_rr=args.min_rr, stride=args.stride,
                             max_hold=args.max_hold, account=args.account)
    if not trades:
        print("无交易记录。")
        return 1

    panel = build_factor_panel(trades, factors=_FACTORS, extra_cols=("decision", "exit_reason"))
    if panel.empty:
        print("无可用样本（可能全部未成交）。")
        return 1
    n_dates = panel["date"].nunique()
    print(f"\n面板：{len(panel)} 条成交样本，{n_dates} 个交易日，"
          f"平均每期 {len(panel) / max(1, n_dates):.1f} 只")
    if n_dates < 8:
        print("  ⚠️ 有效交易日不足 8 个，横截面 IC 的统计意义有限——"
              "请扩大股票池或缩小 stride。")

    # ---- 1) 因子汇总 ----
    summary = validate_factors(panel, _FACTORS, return_col="ret")
    print("\n因子汇总（按 |IC 均值| 降序）")
    print("=" * 108)
    _print_table(summary)

    # ---- 2) 明细 + 滚动前瞻 ----
    detail: dict[str, dict] = {}
    for f in _FACTORS:
        rep = validate_factor(panel, f, "ret", n_quantiles=args.quantiles)
        detail[f] = rep
        print("\n" + "-" * 66)
        print(summarize(rep))

        wf = walk_forward_factor(panel, f, "ret", n_splits=args.splits)
        if not wf.empty:
            kept = int(wf["sign_kept"].sum())
            print(f"  滚动前瞻样本外：{kept}/{len(wf)} 折保持同号")
            for _, r in wf.iterrows():
                ti = r["train_ic"] if np.isfinite(r["train_ic"]) else float("nan")
                si = r["test_ic"] if np.isfinite(r["test_ic"]) else float("nan")
                print(f"    折{r['fold']}  训练IC {ti:>7.3f}  测试IC {si:>7.3f}  "
                      f"{'✅ 延续' if r['sign_kept'] else '❌ 未延续'}")
        detail[f]["walk_forward"] = wf.to_dict("records") if not wf.empty else []

        strat = stratified_ic(panel, f, "ret", "decision")
        if not strat.empty and len(strat) > 1:
            parts = [f"{r['regime']}: IC={r['ic_mean']} (n={r['n_obs']})"
                     for _, r in strat.iterrows()]
            print("  按决策分层：" + "  ".join(parts))
        detail[f]["by_decision"] = strat.to_dict("records") if not strat.empty else []

    # ---- 落盘 ----
    out_dir = Path(__file__).resolve().parents[1] / "results"
    out_path = Path(args.out) if args.out else out_dir / "factor_validation_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "source": source,
            "generated_by": "examples/validate_factors.py",
            # generated_at 供下游「报告过期就不套用」的守卫使用（scoring.factor_weights）。
            # 没有它只能退回文件 mtime —— 复制/同步会改 mtime，口径就不可靠了。
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "args": vars(args),
            "panel": {"n_obs": int(len(panel)), "n_dates": int(n_dates),
                      "codes": sorted(panel["code"].unique().tolist())},
            "summary": summary.to_dict("records"),
            "detail": detail,
        }, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n验证报告已写入 {out_path}")
    print("\n解读要点：IC 均值看方向与量级，IR/t 值看稳定性（注意样本重叠会高估），"
          "\n   分组单调性看能否按因子排序选票，滚动前瞻看样本外是否延续。"
          "\n   verdict 为「无区分力/不稳定/不单调」的因子应考虑降权或从公式中移除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
