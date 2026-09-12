"""权重标定：用历史数据评估 9 因子权重的「推荐准确度」。

目标
----
``stock_score.WEIGHTS``（9 因子，和=1.00）目前是经验手调常数。本脚本用
**pick-rate 指标**对权重做系统化标定，回答「哪个权重组合让 Top-N 推荐
后续涨得更好」。

核心指标：pick-rate（推荐命中率）
---------------------------------
在历史每个窗口 T，对一批股票计算各自的 Stock Score（用某套权重）→ 排序取
Top-N → 看这些股票在 T 之后 future_days 天的实际收益均值。这个指标直接
反映「推荐的名单准不准」，而现有 ``TradingPlanBacktest`` 的指标只评估
入场/止损/目标单笔规则、不参与排序，故无法用于标定权重。

用法
----
    # 真实数据标定（联网拉 6 只 A股 500 天历史，约需几十秒）
    python examples/calibrate_weights.py --codes 600519 000001 601398 000858 002594 601857

    # 合成数据快速演示（离线可测，可入 CI）
    python examples/calibrate_weights.py --synthetic

输出：方案对比表（各扰动方案 vs 基线），并给出推荐权重。
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.scoring.stock_score import WEIGHTS, calc_stock_score

# 因子顺序（保持稳定输出）
FACTORS = list(WEIGHTS.keys())
DEFAULT_W = {k: WEIGHTS[k] for k in FACTORS}

# 扰动幅度（绝对值，加到某因子后其余重新归一化）
PERTURB = 0.06
# 关键因子做双向扰动（±）；次要因子只做 +向（提权测试）
KEY_FACTORS = {"technical", "risk", "fundamental", "capital_flow", "valuation"}
MINOR_FACTORS = {"growth", "momentum", "market_env", "sector"}

FUTURE_DAYS = [20, 60]  # 后市区间（交易日）


def _norm(w: dict) -> dict:
    """归一化到权重和=1.00。"""
    s = sum(max(0.0, v) for v in w.values())
    if s <= 0:
        return DEFAULT_W
    return {k: max(0.0, v) / s for k, v in w.items()}


def perturb_schemes() -> list[dict]:
    """生成候选权重方案：基线 + 每因子扰动一个方向。"""
    schemes = [DEFAULT_W]
    for f in FACTORS:
        for delta in ([PERTURB] if f in MINOR_FACTORS else [-PERTURB, PERTURB]):
            w = {k: v for k, v in DEFAULT_W.items()}
            w[f] = DEFAULT_W[f] + delta
            schemes.append(_norm(w))
    return schemes


# --------------------------------------------------------------------------- #
# 合成数据（离线可测）：几何随机游走 + 可控趋势片
# --------------------------------------------------------------------------- #
def _synthetic_kline(seed: int, days: int = 500, drift: float = 0.0008,
                     vol: float = 0.02) -> pd.DataFrame:
    rng = random.Random(seed)
    closes = []
    p = 100.0
    for i in range(days):
        # 三段式趋势：后段引入差异，让不同 stock 表现不同，Pick-Rate 才有分辨力
        seg = drift * (1.0 + 0.6 * math.sin(i / 40.0))
        p *= math.exp(seg + rng.gauss(0, vol))
        closes.append(p)
    idx = pd.date_range("2024-01-01", periods=days, freq="B")
    s = pd.Series(closes, index=idx)
    df = pd.DataFrame({
        "open": s.shift(1).fillna(s.iloc[0]),
        "high": s * (1 + abs(rng.gauss(0, 0.004))),
        "low": s * (1 - abs(rng.gauss(0, 0.004))),
        "close": s,
        "volume": [1e6 * (0.8 + rng.random() * 0.6) for _ in range(days)],
    })
    return df


# --------------------------------------------------------------------------- #
# 因子数据可用性诊断
# --------------------------------------------------------------------------- #
def _factor_variability(data: dict[str, pd.DataFrame]) -> dict[str, float]:
    """计算每因子在样本末端上的分数变异（跨股票 + 跨窗口）。

    变异 ≈ 0 的因子在当前数据下恒返回同一分（如估值缺数据 → 固定 85 或 50），
    意味着调整其权重不会改变排序——标定时应聚焦有区分力的的因子。
    """
    ready: dict[str, pd.DataFrame] = {}
    for code, raw in data.items():
        ready[code] = add_all_indicators(raw.reset_index(drop=True))

    n_days = min(len(ind) for ind in ready.values())
    samples: dict[str, list[float]] = {k: [] for k in FACTORS}
    stride = max(10, n_days // 12)
    for i in range(120, n_days - 60, stride):
        for code, ind in ready.items():
            sc = calc_stock_score(ind.iloc[: i + 1])
            for k in FACTORS:
                samples[k].append(sc.components[k])
    out = {}
    for k in FACTORS:
        vals = samples[k]
        out[k] = float(np.std(vals)) if vals else 0.0
    return out


# --------------------------------------------------------------------------- #
# Pick-Rate 评估：窗口滚动，Top-N 后续收益
# --------------------------------------------------------------------------- #
def evaluate_scheme(
    data: dict[str, pd.DataFrame],
    w: dict,
    *,
    top_n: int = 3,
    start_day: int = 180,
    stride: int = 20,
) -> dict:
    """对一套权重计算平均 Pick-Rate 收益。

    每个窗口 T：算任股票在当前指标列的 Stock Score → 取 Top-N → 求这些股票
    在 T 之后 future_days 的平均收益。跨所有窗口与 future_days 平均。
    """
    # 指标只算一次（对整段数据），各窗口取 iloc 切片即可（指标是因果的）
    ready: dict[str, pd.DataFrame] = {}
    for code, raw in data.items():
        ind = add_all_indicators(raw.reset_index(drop=True))
        ready[code] = ind

    cumulative = {fd: [] for fd in FUTURE_DAYS}
    total_windows = 0

    n_days = len(next(iter(ready.values())))
    for i in range(start_day, n_days - max(FUTURE_DAYS), stride):
        # 每只股票在窗口 T 的评分
        scored = []
        for code, ind in ready.items():
            hist = ind.iloc[: i + 1]
            score = calc_stock_score(hist, weights=w).total
            scored.append((code, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_n]

        # T 之后的实际收益
        for fd in FUTURE_DAYS:
            j = i + fd
            if j >= n_days:
                continue
            rets = []
            for code, _sc in top:
                close = ready[code]["close"]
                r = (close.iloc[j] / close.iloc[i] - 1) * 100
                rets.append(r)
            if rets:
                cumulative[fd].append(sum(rets) / len(rets))
        total_windows += 1

    out = {"windows": total_windows}
    for fd in FUTURE_DAYS:
        vals = cumulative[fd]
        out[f"ret_{fd}d"] = float(np.mean(vals)) if vals else float("nan")
        out[f"win_{fd}d"] = sum(1 for v in vals if v > 0) / len(vals) if vals else float("nan")
    # 综合得分：60 日收益为主 + 近程命中率
    out["blend"] = (
        out.get("ret_60d", 0.0) * 0.6
        + (out.get("win_20d", 0.0) - 0.5) * 100 * 0.4
        if math.isfinite(out.get("ret_60d", float("nan")))
        else float("-inf")
    )
    return out


# --------------------------------------------------------------------------- #
def _fmt(w: dict) -> str:
    return " ".join(f"{k[:4]}={v:.2f}" for k, v in w.items())


def _scheme_name(w: dict) -> str:
    """给扰动方案一个可读名字：只显示被扰动的主因子（≥2% 权重变化）。

    归一化会把 ±0.01 的补偿噪音摊到所有因子，故阈值取 0.02，只突出
    真正被改变的方向。
    """
    diffs = {k: round(w[k] - DEFAULT_W[k], 3) for k in FACTORS}
    changed = {k: v for k, v in diffs.items() if abs(v) >= 0.02}
    if not changed:
        return "基线"
    parts = []
    for k, v in sorted(changed.items(), key=lambda x: abs(x[1]), reverse=True):
        parts.append(f"{k} {'↑' if v > 0 else '↓'}{abs(v):.2f}")
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="标定 Stock Score 9 因子权重")
    ap.add_argument("--codes", nargs="*", default=[], help="A股代码列表（默认联网拉内置样例）")
    ap.add_argument("--synthetic", action="store_true", help="用合成数据（离线）")
    ap.add_argument("--days", type=int, default=500, help="每只股票历史天数")
    ap.add_argument("--top_n", type=int, default=3, help="每窗口 Top-N")
    ap.add_argument("--stride", type=int, default=20, help="窗口步长（日）")
    args = ap.parse_args()

    # ---- 加载数据 ----
    data: dict[str, pd.DataFrame] = {}
    if args.synthetic:
        for k, seed in enumerate([1, 2, 3, 4, 5, 7]):
            code = f"SIM{k:02d}"
            data[code] = _synthetic_kline(seed, args.days)
        source = "合成数据"
    else:
        codes = args.codes or ["600519", "000001", "601398", "000858", "002594", "601857"]
        source = f"真实数据 {','.join(codes)}"
        for c in codes:
            info = detect_market(c)
            df = fetch_kline(info, days=args.days)
            if df is not None and len(df) > 200:
                data[c] = df
                print(f"  已加载 {c} ({len(df)} 根K线)")
            else:
                print(f"  ⚠️ 跳过 {c}（数据不足）")
    if len(data) < 3:
        print("可用股票太少（需≥3只），无法标定。")
        return 1
    print(f"\n用 {source}（{len(data)} 只）评估权重，Top-{args.top_n}，"
          f"stride={args.stride} 日，后市 {FUTURE_DAYS} 日")

    # ---- 因子数据可用性诊断 ----
    # 某些因子（估值/板块/市场环境/成长）在候选池无数据时返回固定中性 50，
    # 即「权重动了也不影响排序」——把这些真实状态告诉用户，避免误标定。
    _variability = _factor_variability(data)
    _dead = [k for k, v in _variability.items() if v < 1e-6]
    if _dead:
        print(f"  ⚠️ 当前数据下下列因子分数恒定（无区分力，权重改动不影响排序）: "
              f"{', '.join(_dead)}")
    print()

    print(f"{'方案':<32} {'60日收益%':>10} {'60日命中':>8} {'20日命中':>8} {'综合分':>8}")
    print("-" * 70)

    schemes = perturb_schemes()
    results = []
    for w in schemes:
        r = evaluate_scheme(data, w, top_n=args.top_n, stride=args.stride)
        results.append((w, r))
        rm = r.get("ret_60d", float("nan"))
        print(f"{_scheme_name(w):<32} {rm if math.isfinite(rm) else float('nan'):>10.2f} "
              f"{r.get('win_60d', float('nan')):>8.0%} "
              f"{r.get('win_20d', float('nan')):>8.0%} "
              f"{r.get('blend', float('-inf')):>8.2f}")

    # ---- 排名 ----
    best_w, best_r = max(results, key=lambda x: x[1]["blend"])
    print("\n最佳方案：")
    print("  " + _scheme_name(best_w))
    for k in FACTORS:
        mark = " ←" if abs(best_w[k] - DEFAULT_W[k]) > 1e-3 else ""
        print(f"    {k:<13} {DEFAULT_W[k]:.2f} → {best_w[k]:.2f}{mark}")

    # 写入建议权重到 results/calibrated_weights.json
    out_dir = Path(__file__).resolve().parents[1] / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "calibrated_weights.json"
    import json

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"baseline": DEFAULT_W, "recommended": best_w,
                   "metrics": {k: v for k, v in best_r.items()},
                   "source": source}, f, ensure_ascii=False, indent=2)
    print(f"\n建议权重已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
