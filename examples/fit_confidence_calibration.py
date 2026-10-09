"""置信度标定：把引擎里的 confidence 从「打分」标定成「概率」。

要解决的问题
------------
``OpportunityEngine`` 的 confidence 是手工线性加权：

    confidence = 0.6 * (机会分 / 100) + 0.4 * min(RR, 4) / 4

它单调、但没有校准。Dashboard 上显示「置信度 70%」时，实际成功率可能是 45%
也可能是 85%——因此「置信度低于多少就降级为 WATCH」这个阈值无法理性设定。

本脚本用历史回测产出 (置信度 → 实际结果) 配对样本，拟合一个单调映射把它变成
真概率，并给出标定前后的 ECE / Brier 对比与可靠性表。

用法
----
    # 真实数据（联网拉多只 A 股）
    python examples/fit_confidence_calibration.py --codes 600519 000001 601398 000858 002594 601857

    # 合成数据（离线，可入 CI）
    python examples/fit_confidence_calibration.py --synthetic

产物：``results/confidence_calibration.json``，由引擎按需加载（文件不存在则
保持现有行为，见 ``OpportunityEngine(calibrator=...)``）。

⚠️ 两个必须知道的偏差
---------------------
1. **样本重叠**：stride 小于持有期时，同一段行情被反复计入，样本不独立，ECE 会
   偏乐观。脚本会在 stride 过小时告警，交叉验证默认用**滚动前瞻**（walk-forward，
   训练集严格早于测试集，见 ``--cv``）而非随机划分，避免「用未来数据评估过去」的
   前视泄漏；``--embargo`` 可进一步隔离紧邻测试段的训练样本。
2. **选择偏差**：未成交（价格从未进区间）的计划被排除——它们是「没执行」而不是
   「亏了」。但高置信度的计划往往现价就在区间内、更容易成交，所以标定结果应理解
   为「若按计划买入」的条件概率。脚本按决策类型分组给出基础成功率，便于自查。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.calibration import auc_score, fit_calibrator
from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine

DEFAULT_CODES = ["600519", "000001", "601398", "000858", "002594", "601857"]


# --------------------------------------------------------------------------- #
def _synthetic_df(seed: int, days: int) -> pd.DataFrame:
    """几何随机游走合成日K，供离线验证脚本本身可用。"""
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0004, 0.0006)          # 每只票趋势不同 → 成功率有差异
    vol = rng.uniform(0.012, 0.028)
    ret = rng.normal(drift, vol, days)
    close = 10.0 * np.exp(np.cumsum(ret))
    intraday = np.abs(rng.normal(0, vol / 2, days))
    volume = rng.uniform(1e6, 5e6, days)
    return pd.DataFrame(
        {
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close * (1 + intraday),
            "low": close * (1 - intraday),
            "close": close,
            "volume": volume,
            "amount": volume * close,
            "date": pd.date_range("2023-01-02", periods=days, freq="B"),
        }
    )


def _collect_trades(
    data: dict, *, min_rr: float, stride: int, max_hold: int, account: float
) -> list:
    """对每只股票跑回测，汇总所有交易记录。"""
    trades: list = []
    for code, raw in data.items():
        df = add_all_indicators(raw)
        engine = OpportunityEngine(account_equity=account, regime_score=65)
        bt = TradingPlanBacktest(engine=engine, min_rr=min_rr,
                                 max_hold_days=max_hold, stride=stride)
        res = bt.run(df, code, code)
        if res.trades:
            trades.extend(res.trades)
            print(f"  {code}: {len(res.trades)} 笔计划")
        else:
            print(f"  {code}: 无有效样本")
    return trades


def _build_samples(trades: list, dead_zone: float) -> tuple[dict, np.ndarray, dict]:
    """把交易记录转成特征数组。

    返回 ``(features, labels, stats)``。``features`` 含 ``confidence`` 以及各原始
    成分（机会分 / 个股分 / RR）——置信度是它们的加权混合，**单独看混合值无法判断
    是哪个成分没信号**，分成分算 AUC 才能定位该改哪里。
    """
    feat: dict[str, list] = {"confidence": [], "opportunity_score": [],
                             "stock_score": [], "risk_reward_1": []}
    labels: list[int] = []
    order: list[float] = []
    stats = {
        "total": len(trades),
        "not_entered": 0,
        "no_confidence": 0,
        "dead_zone": 0,
        "unparsed_dates": 0,
        "by_decision": {},
        "by_exit": {},
    }

    for t in trades:
        d = t.to_dict() if hasattr(t, "to_dict") else t
        conf = d.get("confidence")
        if not d.get("entry_executed"):
            stats["not_entered"] += 1
            continue
        if conf is None:
            stats["no_confidence"] += 1
            continue
        lbl = t.outcome_label(dead_zone) if hasattr(t, "outcome_label") else None
        if lbl is None:
            stats["dead_zone"] += 1
            continue

        for k in feat:
            feat[k].append(d.get(k))
        labels.append(int(lbl))
        try:
            order.append(pd.Timestamp(d.get("date")).value)
        except (ValueError, TypeError):
            # 日期不可解析 → 退化为追加顺序。多股票拼接后这不是时间序，
            # 时序分块交叉验证会失去意义，必须让用户知道。
            order.append(float(len(order)))
            stats["unparsed_dates"] += 1

        dec = d.get("decision", "?")
        bucket = stats["by_decision"].setdefault(dec, {"n": 0, "wins": 0})
        bucket["n"] += 1
        bucket["wins"] += int(lbl)
        stats["by_exit"][d.get("exit_reason", "?")] = \
            stats["by_exit"].get(d.get("exit_reason", "?"), 0) + 1

    return (
        {k: np.asarray(v, dtype=float) for k, v in feat.items()},
        np.asarray(labels, dtype=float),
        np.asarray(order, dtype=float),
        stats,
    )


# 特征可读名
_FEATURE_LABELS = {
    "confidence": "置信度（混合值）",
    "opportunity_score": "机会评分（0~100）",
    "stock_score": "个股评分（0~100）",
    "risk_reward_1": "风险收益比 RR",
}


def _print_discrimination(features: dict, labels: np.ndarray) -> None:
    """分成分算 AUC，定位「该改哪个成分」。

    置信度是 0.6×机会分 + 0.4×RR 的线性混合，混合后 AUC 无信号不代表成分也无信号
    ——两个成分可能方向相反、互相抵消。
    """
    print("\n打分辨识力诊断（AUC 0.5 = 对盈亏无区分力，>0.55 才算有用）")
    print(f"{'成分':<20} {'AUC':>8} {'0.7分位胜率':>12} {'0.3分位胜率':>12} {'单调?':>7}")
    print("-" * 64)
    for key, name in _FEATURE_LABELS.items():
        v = features.get(key)
        if v is None or v.size == 0 or np.all(~np.isfinite(v)):
            print(f"{name:<20} {'n/a':>8}")
            continue
        auc = auc_score(v, labels)
        lo, hi = np.percentile(v, 30), np.percentile(v, 70)
        low_grp = labels[v <= lo]
        high_grp = labels[v >= hi]
        wr_lo = float(low_grp.mean()) if low_grp.size else float("nan")
        wr_hi = float(high_grp.mean()) if high_grp.size else float("nan")
        # 高分组胜率应当明显高于低分组，才说明打分方向有意义
        monotone = "是" if (wr_hi - wr_lo) > 0.05 else ("反了" if (wr_hi - wr_lo) < -0.05 else "否")
        print(f"{name:<20} {_fmt(auc):>8} {wr_hi:>12.1%} {wr_lo:>12.1%} {monotone:>7}")


# --------------------------------------------------------------------------- #
def _fmt(v, nd: int = 4) -> str:
    """格式化可能为 None 的数值。"""
    return "n/a" if v is None else f"{v:.{nd}f}"


def _print_reliability(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"{'分箱':>10} {'样本':>6} {'预测均值':>10} {'实际频率':>10} {'偏差':>9}")
    print("-" * 50)
    for r in rows:
        if not r["count"]:
            continue
        print(f"{r['left']:>4.1f}~{r['right']:<4.1f} {r['count']:>6} "
              f"{r['mean_pred']:>10.3f} {r['actual_freq']:>10.3f} {r['gap']:>+9.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="置信度概率标定")
    ap.add_argument("--codes", nargs="*", default=[], help="A股代码列表")
    ap.add_argument("--synthetic", action="store_true", help="用合成数据（离线）")
    ap.add_argument("--days", type=int, default=900, help="每只股票K线天数")
    ap.add_argument("--stride", type=int, default=5, help="每隔 N 日生成一个计划")
    ap.add_argument("--min-rr", type=float, default=1.5, help="只统计 RR ≥ 该值的计划")
    ap.add_argument("--max-hold", type=int, default=60, help="最长持有交易日")
    ap.add_argument("--dead-zone", type=float, default=0.3,
                    help="收益绝对值小于该值(%%)的样本视为噪声，剔除")
    ap.add_argument("--method", default="auto",
                    choices=["auto", "platt", "isotonic", "identity"])
    ap.add_argument("--folds", type=int, default=5, help="交叉验证折数")
    ap.add_argument("--cv", default="auto",
                    choices=["auto", "walk_forward", "time_block", "random"],
                    help="交叉验证策略。auto=有时间序时用滚动前瞻（无前视泄漏）；"
                         "time_block/random 为旧口径，仅作对照（存在泄漏）")
    ap.add_argument("--embargo", type=int, default=0,
                    help="滚动前瞻中从训练集尾部剔除的样本数（按时间序），"
                         "建议设为 max_hold 量级以隔离相邻重叠样本")
    ap.add_argument("--bins", type=int, default=10, help="ECE 分箱数")
    ap.add_argument("--account", type=float, default=100_000)
    ap.add_argument("--out", default="", help="输出路径（默认 results/confidence_calibration.json）")
    args = ap.parse_args()

    # ---- 数据 ----
    data: dict = {}
    if args.synthetic:
        for k, seed in enumerate([1, 2, 3, 4, 5, 7, 11, 13]):
            data[f"SIM{k:02d}"] = _synthetic_df(seed, args.days)
        source = "合成数据(离线)"
    else:
        codes = args.codes or DEFAULT_CODES
        source = f"真实数据 {','.join(codes)}"
        for c in codes:
            info = detect_market(c)
            df = fetch_kline(info, days=args.days)
            if df is not None and len(df) > 200:
                data[c] = df
            else:
                print(f"  ⚠️ 跳过 {c}（数据不足）")

    if not data:
        print("无可用数据。")
        return 1

    print(f"\n数据源：{source}（{len(data)} 只）")
    print(f"回测参数：stride={args.stride} min_rr={args.min_rr} max_hold={args.max_hold}")

    # ---- 样本重叠告警 ----
    if args.stride < args.max_hold / 2:
        print(f"  ⚠️ stride={args.stride} 明显小于持有期 {args.max_hold}，回测样本高度重叠、"
              f"互相不独立，\n     ECE 会偏乐观（交叉验证已按时间分块，但仍无法完全消除）。"
              f"\n     要发布标定参数前，建议至少用 stride≥{args.max_hold // 2} 复跑一次核对结论是否稳定。")

    print("\n回测中…")
    trades = _collect_trades(data, min_rr=args.min_rr, stride=args.stride,
                             max_hold=args.max_hold, account=args.account)

    features, labels, order, stats = _build_samples(trades, args.dead_zone)
    scores = features["confidence"]
    if scores.size == 0:
        print("无可用标定样本。")
        return 1

    print(f"\n样本筛选：计划 {stats['total']} 笔 → 可用 {scores.size} 笔"
          f"（未成交 {stats['not_entered']}、缺置信度 {stats['no_confidence']}、"
          f"收益落噪声带 {stats['dead_zone']}）")

    if stats["unparsed_dates"]:
        print(f"  ⚠️ 有 {stats['unparsed_dates']} 条记录的日期无法解析，时序分块交叉验证"
              f"已退化为按追加顺序分块——\n     多股票场景下这不再是时间序，CV 结果不可信。"
              f"请检查回测记录的 date 字段。")

    print("\n基础成功率（标定前，按决策类型分组）：")
    print(f"{'决策':<20} {'样本':>6} {'实际盈利占比':>12}")
    print("-" * 42)
    for dec, b in sorted(stats["by_decision"].items(), key=lambda x: -x[1]["n"]):
        print(f"{dec:<20} {b['n']:>6} {b['wins'] / b['n']:>12.1%}")

    # ---- 拟合（默认滚动前瞻交叉验证：训练集严格早于测试集，防前视泄漏）----
    calibrator, report = fit_calibrator(
        scores, labels, method=args.method, n_folds=args.folds,
        order=order, n_bins=args.bins, cv=args.cv, embargo=args.embargo,
    )

    print("\n" + "=" * 62)
    print("标定结果")
    print("=" * 62)
    # 前后必须同一子集比较：滚动前瞻只覆盖部分样本（首段无训练集），用全样本的
    # *_before 去比子集的 *_after 会得出错误结论。优先用 *_before_cv。
    def _before(key: str):
        return report.get(key + "_cv", report.get(key))

    print(f"{'指标':<28} {'标定前(CV子集)':>14} {'标定后(CV)':>14}")
    print("-" * 62)
    print(f"{'ECE (越低越好)':<28} {_fmt(_before('ece_before')):>14} {_fmt(report['ece_after_cv']):>14}")
    print(f"{'Brier (越低越好)':<28} {_fmt(_before('brier_before')):>14} {_fmt(report['brier_after_cv']):>14}")
    print(f"{'AUC (0.5 = 无区分力)':<28} {_fmt(_before('auc_before')):>14} "
          f"{_fmt(report.get('auc_after_cv')):>14}")
    print(f"{'置信度跨度 5%~95%':<28} {_fmt(_before('spread_before')):>14} "
          f"{_fmt(report.get('spread_after_cv')):>14}")
    print(f"\n方法：{report['method']}    交叉验证：{report['cv_strategy']} / {report['cv_folds']} 折"
          f"    CV 覆盖：{report.get('cv_covered', '-')}/{report['n_samples']} 样本"
          f"    embargo：{report.get('cv_embargo', 0)}"
          f"    基础成功率：{report['base_rate']:.1%}")
    if report.get("cv_note"):
        print(f"  ⚠️ {report['cv_note']}")
    if report.get("diagnosis"):
        print(f"\n⚠️ {report['diagnosis']}")
    if report.get("rejected_method"):
        print(f"\n⚠️ 已否决「{report['rejected_method']}」并回退为不标定：{report['fell_back']}")
    if report.get("params", {}).get("platt"):
        p = report["params"]["platt"]
        print(f"Platt 参数：a={p['a']}（<1 说明原打分为过度自信，需收缩）  b={p['b']}")
        print(f"  → 解读：原置信度 0.70 标定后约 "
              f"{calibrator.apply(0.70):.3f}；0.90 标定后约 {calibrator.apply(0.90):.3f}")

    _print_reliability(report["reliability_before"], "可靠性表（标定前）")
    _print_reliability(report["reliability_after_cv"], "可靠性表（标定后，out-of-fold）")

    # 混合后的置信度没信号 ≠ 每个成分都没信号（可能方向相反互相抵消）→ 拆开看
    _print_discrimination(features, labels)

    # ---- 落盘 ----
    # 合成数据拟合出的参数绝不能冒充真实标定结果：默认换成带 SYNTHETIC 后缀的
    # 文件名，并把数据来源写进 meta，双保险。
    out_dir = Path(__file__).resolve().parents[1] / "results"
    if args.out:
        out_path = Path(args.out)
    elif args.synthetic:
        out_path = out_dir / "confidence_calibration.SYNTHETIC.json"
    else:
        out_path = out_dir / "confidence_calibration.json"

    side_path = out_path.with_name(out_path.stem + "_report.json")

    # 评估报告无论结论如何都落盘：它是「为什么（没）标定」的证据，需要留痕可追溯。
    with open(side_path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "generated_by": "examples/fit_confidence_calibration.py",
                   "args": vars(args), "sample_stats": stats, "report": report},
                  f, ensure_ascii=False, indent=2)

    # ⚠️ 只有真的拟合出可用映射才写标定参数文件。该文件的「存在与否」就是引擎是否
    # 标定的开关（见 load_default_calibrator），把 identity 也写进去等于伪造了一个
    # 「已标定」的状态，后续排查会被严重误导。
    if report["method"] == "identity":
        if out_path.exists():
            print(f"\n⚠️ 检测到已存在的标定文件 {out_path}")
            print("   本次结论是不标定，故不覆盖它；但它的参数来自更早的数据，"
                  "\n   请确认是否仍应生效——不需要就手动删除，引擎即回到未标定状态。")
        print(f"\n本次结论：不标定（identity），未写入 {out_path}")
        print("   含义：当前置信度已无「简单单调映射」可改善，问题在打分本身。"
              "\n   请对照上表的成分诊断，改进机会分/个股分/RR 的构造或权重，再重跑本脚本。")
        print(f"\n评估报告（含全部诊断）已写入 {side_path}")
        return 0

    calibrator.meta["source"] = source
    calibrator.save(out_path)

    print(f"\n标定参数已写入 {out_path}")
    print(f"评估报告已写入 {side_path}")
    print("\n下一步：让引擎加载该文件，confidence 即按概率解释。"
          "\n   注意标定参数有市场状态时效性，建议每季度用最近数据重新拟合。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
