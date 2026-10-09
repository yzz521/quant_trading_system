"""回测可信度审计：把「回测显示正期望」拆成「真的正期望」吗。

背景
----
2026-09-22 跑真实数据标定时发现：本仓库回测报告每笔 **+2.59%** 的正期望，
几乎全部来自**买不到的成交价**。本脚本把那次诊断固化成可重复执行的流程。

四项检查（按危害排序，任何一项不过都会推翻收益结论）
----------------------------------------------------
1. **成交可达性**——限价单只在价格真的触及入场价时才成交。原 `_simulate` 只判
   `low <= entry_high`，而 entry_high 在现价**之上**、成交价却取现价**之下**的支撑位，
   缺了「当日是否真跌到 entry_price」这一步 → 系统性以幽灵价格建仓。
2. **止损几何**——止损距离若与波动率无关（被压到常数级），会被日常噪声机械性扫掉。
3. **跳空穿透口径**——「触发止损即以止损价成交」会低估隔夜跳空造成的亏损。
4. **打分成因区分力**——混合分数无信号 ≠ 成分无信号；拆开算 AUC 才知道该改哪一块。

用法
----
    python examples/audit_backtest_realism.py --codes 600519 000001 601398 \
        000858 002594 601857 600104 600000 --days 1200 --stride 5

    python examples/audit_backtest_realism.py --synthetic      # 离线自测

⚠️ 本脚本**只读**：不修改任何回测源码，通过替换 `_simulate` 的局部实现做口径对照，
   并明确标注差异只在于成交价那一行。样本重叠（stride < 持有期）依旧存在，
   所以这里的数字是「方向性判据」而非精确估计。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest  # noqa: E402
from quant_trading_system.stock_analysis.backtest.trading_plan_backtest import (  # noqa: E402
    BacktestTrade,
)
from quant_trading_system.stock_analysis.calibration import auc_score  # noqa: E402
from quant_trading_system.stock_analysis.data_fetcher import (  # noqa: E402
    detect_market,
    fetch_kline,
)
from quant_trading_system.stock_analysis.indicators import add_all_indicators  # noqa: E402
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine  # noqa: E402

DEFAULT_CODES = ["600519", "000001", "601398", "000858", "002594", "601857", "600104", "600000"]
_ORIG_SIMULATE = TradingPlanBacktest._simulate


# --------------------------------------------------------------------------- #
def _synthetic_df(seed: int, days: int) -> pd.DataFrame:
    """几何随机游走合成日K，供离线自测。"""
    rng = np.random.default_rng(seed)
    drift, vol = rng.normal(0.0004, 0.0006), rng.uniform(0.012, 0.028)
    ret = rng.normal(drift, vol, days)
    close = 20.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, vol * 0.6, days)))
    low = close * (1 - np.abs(rng.normal(0, vol * 0.6, days)))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, vol * 0.3, days))
    return pd.DataFrame({
        "date": pd.bdate_range("2019-01-01", periods=days),
        "open": open_, "high": np.maximum.reduce([high, open_, close]),
        "low": np.minimum.reduce([low, open_, close]), "close": close,
        "volume": rng.uniform(1e6, 5e6, days),
    })


def _collect(data: dict, *, min_rr: float, stride: int, max_hold: int, account: float) -> list:
    trades: list = []
    for code, raw in data.items():
        df = add_all_indicators(raw)
        engine = OpportunityEngine(account_equity=account, regime_score=65)
        res = TradingPlanBacktest(engine=engine, min_rr=min_rr,
                                  max_hold_days=max_hold, stride=stride).run(df, code, code)
        trades.extend(res.trades)
    return trades


def _probe_simulate(self, trade: BacktestTrade, future: pd.DataFrame) -> None:
    """原逻辑 + 记录入场日行情，用于判定限价单是否真能成交。

    与 `_ORIG_SIMULATE` 的唯一差别：多写 4 个探针字段（entry_day_open/low/high、days_to_entry）。
    """
    if future is None or future.empty:
        return
    open_ = future["open"].astype(float)
    high = future["high"].astype(float)
    low = future["low"].astype(float)
    close = future["close"].astype(float)

    entry_exec_price = None
    entry_day = None
    for j in range(len(future)):
        o, h, lo = float(open_.iloc[j]), float(high.iloc[j]), float(low.iloc[j])
        if entry_exec_price is None:
            if lo <= trade.entry_high:
                entry_exec_price = (min(o, trade.entry_price)
                                    if o <= trade.entry_high else trade.entry_price)
                entry_exec_price = max(entry_exec_price, trade.entry_low)
                entry_day = j
                trade.entry_executed = True
                trade.entry_exec_price = round(entry_exec_price, 2)
                trade.entry_day_open, trade.entry_day_low = o, lo
                trade.entry_day_high, trade.days_to_entry = h, j
                trade.hit_target_1 = h >= trade.target_1
                trade.hit_target_2 = h >= trade.target_2
                if lo <= trade.stop_loss:
                    trade.exit_reason = "stop_loss"
                    trade.holding_days = 1
                    trade.return_pct = round((trade.stop_loss / entry_exec_price - 1) * 100, 2)
                    return
                if h >= trade.target_2:
                    trade.exit_reason = "target_2"
                    trade.holding_days = 1
                    trade.return_pct = round((trade.target_2 / entry_exec_price - 1) * 100, 2)
                    return
            continue
        trade.hit_target_1 = trade.hit_target_1 or h >= trade.target_1
        trade.hit_target_2 = trade.hit_target_2 or h >= trade.target_2
        if lo <= trade.stop_loss:
            trade.exit_reason = "stop_loss"
            trade.holding_days = j - entry_day + 1
            trade.return_pct = round((trade.stop_loss / entry_exec_price - 1) * 100, 2)
            return
        if h >= trade.target_2:
            trade.exit_reason = "target_2"
            trade.holding_days = j - entry_day + 1
            trade.return_pct = round((trade.target_2 / entry_exec_price - 1) * 100, 2)
            return
    if entry_exec_price is not None:
        trade.exit_reason = "timeout"
        trade.holding_days = len(future) - entry_day
        trade.return_pct = round((float(close.iloc[-1]) / entry_exec_price - 1) * 100, 2)
    else:
        trade.exit_reason = "not_entered"
        trade.return_pct = 0.0


def _gapaware_simulate(self, trade: BacktestTrade, future: pd.DataFrame) -> None:
    """原逻辑 + 跳空口径：止损取 min(开盘, 止损价)，目标取 max(开盘, 目标价)。

    与 `_ORIG_SIMULATE` 的唯一差别：两处成交价。其余逐字一致。
    """
    if future is None or future.empty:
        return
    open_ = future["open"].astype(float)
    high = future["high"].astype(float)
    low = future["low"].astype(float)
    close = future["close"].astype(float)

    entry_exec_price = None
    entry_day = None
    for j in range(len(future)):
        o, h, lo = float(open_.iloc[j]), float(high.iloc[j]), float(low.iloc[j])
        if entry_exec_price is None:
            if lo <= trade.entry_high:
                entry_exec_price = (min(o, trade.entry_price)
                                    if o <= trade.entry_high else trade.entry_price)
                entry_exec_price = max(entry_exec_price, trade.entry_low)
                entry_day = j
                trade.entry_executed = True
                trade.entry_exec_price = round(entry_exec_price, 2)
                trade.hit_target_1 = h >= trade.target_1
                trade.hit_target_2 = h >= trade.target_2
                if lo <= trade.stop_loss:
                    trade.gapped = o < trade.stop_loss
                    fill = min(o, trade.stop_loss)
                    trade.exit_reason = "stop_loss"
                    trade.holding_days = 1
                    trade.return_pct = round((fill / entry_exec_price - 1) * 100, 2)
                    return
                if h >= trade.target_2:
                    trade.exit_reason = "target_2"
                    trade.holding_days = 1
                    trade.return_pct = round((max(o, trade.target_2) / entry_exec_price - 1) * 100, 2)
                    return
            continue
        trade.hit_target_1 = trade.hit_target_1 or h >= trade.target_1
        trade.hit_target_2 = trade.hit_target_2 or h >= trade.target_2
        if lo <= trade.stop_loss:
            trade.gapped = o < trade.stop_loss
            fill = min(o, trade.stop_loss)
            trade.exit_reason = "stop_loss"
            trade.holding_days = j - entry_day + 1
            trade.return_pct = round((fill / entry_exec_price - 1) * 100, 2)
            return
        if h >= trade.target_2:
            trade.exit_reason = "target_2"
            trade.holding_days = j - entry_day + 1
            trade.return_pct = round((max(o, trade.target_2) / entry_exec_price - 1) * 100, 2)
            return
    if entry_exec_price is not None:
        trade.exit_reason = "timeout"
        trade.holding_days = len(future) - entry_day
        trade.return_pct = round((float(close.iloc[-1]) / entry_exec_price - 1) * 100, 2)
    else:
        trade.exit_reason = "not_entered"
        trade.return_pct = 0.0


def _pct_row(values, label: str) -> None:
    a = np.asarray(values, dtype=float)
    q = np.percentile(a, [5, 25, 50, 75, 95])
    print(f"  {label:<22} p5 {q[0]:>7.2f}  p25 {q[1]:>7.2f}  中位 {q[2]:>7.2f}  "
          f"p75 {q[3]:>7.2f}  p95 {q[4]:>7.2f}")


def _expectancy(trades: list) -> tuple[float, float, int]:
    r = np.array([t.return_pct for t in trades])
    return float(r.mean()), float((r > 0).mean()), int(r.size)


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="回测可信度审计（只读）")
    ap.add_argument("--codes", nargs="*", default=[], help="A股代码列表")
    ap.add_argument("--synthetic", action="store_true", help="用合成数据（离线自测）")
    ap.add_argument("--days", type=int, default=1200)
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--min-rr", type=float, default=1.5)
    ap.add_argument("--max-hold", type=int, default=60)
    ap.add_argument("--dead-zone", type=float, default=0.3,
                    help="收益绝对值小于该值(%%)的样本不计入区分力评估（噪声带）")
    ap.add_argument("--account", type=float, default=100_000)
    args = ap.parse_args()

    if args.synthetic:
        data = {f"SIM{k:02d}": _synthetic_df(s, args.days)
                for k, s in enumerate([1, 2, 3, 4, 5, 7, 11, 13])}
        source = "合成数据(离线)"
    else:
        codes = args.codes or DEFAULT_CODES
        data = {}
        for c in codes:
            df = fetch_kline(detect_market(c), days=args.days)
            if df is not None and len(df) > 200:
                data[c] = df
            else:
                print(f"  ⚠️ 跳过 {c}（数据不足）")
        source = f"真实数据 {','.join(codes)}"

    if not data:
        print("无可用数据。")
        return 1
    print(f"\n数据源：{source}（{len(data)} 只）")
    print(f"回测参数：stride={args.stride} min_rr={args.min_rr} max_hold={args.max_hold}")
    if args.stride < args.max_hold / 2:
        print(f"  ⚠️ stride={args.stride} < 持有期一半，回测样本高度重叠；"
              f"下列数字是方向性判据，不是精确估计。")

    kw = dict(min_rr=args.min_rr, stride=args.stride, max_hold=args.max_hold,
              account=args.account)

    # 探针版：测成交可达性
    BacktestTrade.entry_day_open = 0.0
    BacktestTrade.entry_day_low = 0.0
    BacktestTrade.entry_day_high = 0.0
    BacktestTrade.days_to_entry = 0
    TradingPlanBacktest._simulate = _probe_simulate
    raw = _collect(data, **kw)
    # 跳空版：测跳空口径
    BacktestTrade.gapped = False
    TradingPlanBacktest._simulate = _gapaware_simulate
    gap_trades = _collect(data, **kw)
    TradingPlanBacktest._simulate = _ORIG_SIMULATE

    plans = len(raw)
    traded = [t for t in raw if t.entry_executed]
    if not traded:
        print("无有效交易。")
        return 1

    print("\n" + "=" * 74)
    print(f"【检查 1】成交可达性    计划 {plans} 笔 → 判定成交 {len(traded)} 笔"
          f" ({len(traded)/plans:.1%})")
    print("=" * 74)
    reached_open = [t for t in traded if t.entry_day_open <= t.entry_price + 1e-9]
    ghost = [t for t in traded
             if t.entry_day_open > t.entry_price + 1e-9
             and t.entry_day_low > t.entry_price + 1e-9]
    mid = len(traded) - len(reached_open) - len(ghost)
    total_ret = np.sum([t.return_pct for t in traded])
    print(f"  开盘价 ≤ 入场价（按开盘成交）        {len(reached_open):>5} 笔 {len(reached_open)/len(traded):>6.1%}")
    print(f"  盘中触及入场价（限价成交）            {mid:>5} 笔 {mid/len(traded):>6.1%}")
    print(f"  ⚠️ 两者都高于入场价（**不可达**）      {len(ghost):>5} 笔 {len(ghost)/len(traded):>6.1%}")
    if ghost:
        g_ret = np.sum([t.return_pct for t in ghost])
        print(f"\n  幽灵成交贡献收益 {g_ret:>+9.1f}% / 全部 {total_ret:>+9.1f}% "
              f"= {g_ret/total_ret if total_ret else float('nan'):>7.1%}")
        under = np.array([(t.entry_price / t.entry_day_low - 1) * 100 for t in ghost])
        print(f"  入场价低于当日最低价的中位幅度 {np.median(under):>5.2f}%  ← 凭空的价差")
    verdict_fill = "❌ 不合格：成交价系统性不可达，收益结论作废" if len(ghost) / len(traded) > 0.10 \
        else "✅ 通过：绝大多数成交可达"
    print(f"\n  → {verdict_fill}")

    ghost_ids = {id(t) for t in ghost}
    real = [t for t in traded if id(t) not in ghost_ids]
    e_all, w_all, _ = _expectancy(traded)
    e_real, w_real, n_real = _expectancy(real)
    print(f"\n  {'口径':<26} {'笔数':>6} {'每笔均%':>10} {'胜率':>8}")
    print("  " + "-" * 54)
    print(f"  {'原口径（含不可达成交）':<26} {len(traded):>6} {e_all:>+10.2f} {w_all:>8.1%}")
    print(f"  {'剔除不可达成交':<26} {n_real:>6} {e_real:>+10.2f} {w_real:>8.1%}")
    print(f"  期望变化 {e_real - e_all:>+.2f} 个百分点")

    print("\n" + "=" * 74)
    print("【检查 2】止损几何")
    print("=" * 74)
    _pct_row([(t.stop_loss / t.entry_exec_price - 1) * 100 for t in traded], "止损距离 %")
    _pct_row([(t.target_2 / t.entry_exec_price - 1) * 100 for t in traded], "目标2距离 %")
    stop_d = np.abs([(t.stop_loss / t.entry_exec_price - 1) * 100 for t in traded])
    print(f"  止损距成交价 < 0.5% 的占比 {np.mean(stop_d < 0.5):>6.1%}   "
          f"< 1.5% 的占比 {np.mean(stop_d < 1.5):>6.1%}")
    reasons = {}
    for t in traded:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    top = max(reasons.items(), key=lambda kv: kv[1])
    print(f"  主要出场原因：{top[0]} {top[1]} 笔（{top[1]/len(traded):.1%}）")
    sl = [t for t in traded if t.exit_reason == "stop_loss"]
    amb = sum(1 for t in sl if t.hit_target_2)
    print(f"  止损笔中「同 bar 也曾触及目标2」的 {amb} 笔 → 保守排序造成的高估 "
          f"{'可忽略' if amb / max(len(sl), 1) < 0.05 else '⚠️ 需注意'}")
    verdict_geo = "❌ 不合格：止损距离已压缩到噪声级，与波动率脱钩" \
        if np.median(stop_d) < 1.0 else "✅ 通过：止损距离处在合理量级"
    print(f"\n  → {verdict_geo}")

    print("\n" + "=" * 74)
    print("【检查 3】跳空穿透口径")
    print("=" * 74)
    gt = [t for t in gap_trades if t.entry_executed]
    e_gap, w_gap, n_gap = _expectancy(gt)
    sl_gap = [t for t in gt if t.exit_reason == "stop_loss"]
    gapped = [t for t in sl_gap if getattr(t, "gapped", False)]
    print(f"  跳空穿透止损 {len(gapped)} / {len(sl_gap)} 笔")
    print(f"  {'口径':<26} {'笔数':>6} {'每笔均%':>10} {'胜率':>8}")
    print("  " + "-" * 54)
    print(f"  {'止损=止损价（原）':<26} {len(traded):>6} {e_all:>+10.2f} {w_all:>8.1%}")
    print(f"  {'跳空时按开盘价成交':<26} {n_gap:>6} {e_gap:>+10.2f} {w_gap:>8.1%}")
    delta = e_gap - e_all
    print(f"\n  → {'❌ 不合格：跳空显著低估亏损' if delta < -0.2 else '✅ 该假设被否证：跳空不是主要偏差来源'}"
          f"（每笔差 {delta:+.2f}pp）")

    print("\n" + "=" * 74)
    print("【检查 4】打分成因区分力（AUC，0.5 = 无区分力）")
    print("=" * 74)
    # 标签口径与 fit_confidence_calibration.py 保持一致：剔除收益落在噪声带
    # (|return_pct| < dead_zone) 的样本，再按 正/负 记 1/0。否则「不涨不跌」会被
    # 当成一次失败，同一份数据会算出两个不同的 AUC。
    graded = [t for t in traded if abs(t.return_pct) >= args.dead_zone]
    print(f"  样本：{len(traded)} 笔判定成交 → 剔除噪声带(|收益| < {args.dead_zone}%)后 {len(graded)} 笔")
    feats = {
        "置信度（混合值）": [t.confidence for t in graded],
        "机会评分": [t.opportunity_score for t in graded],
        "个股评分": [t.stock_score for t in graded],
        "风险收益比 RR": [t.risk_reward_1 for t in graded],
    }
    labels = np.array([1 if t.return_pct > 0 else 0 for t in graded], dtype=float)
    print(f"  {'成分':<18} {'AUC':>7} {'高分组':>8} {'低分组':>8}   方向")
    print("  " + "-" * 56)
    weak = []
    for name, vals in feats.items():
        v = np.asarray([x for x in vals], dtype=float)
        if v.size == 0 or not np.all(np.isfinite(v)):
            print(f"  {name:<18} {'n/a':>7}")
            continue
        auc = auc_score(v, labels)
        hi = labels[v >= np.percentile(v, 70)]
        lo = labels[v <= np.percentile(v, 30)]
        wr_hi = float(hi.mean()) if hi.size else float("nan")
        wr_lo = float(lo.mean()) if lo.size else float("nan")
        d = wr_hi - wr_lo
        direction = "正向" if d > 0.05 else ("反向" if d < -0.05 else "无")
        if auc < 0.55:
            weak.append(name)
        print(f"  {name:<18} {auc:>7.4f} {wr_hi:>8.1%} {wr_lo:>8.1%}   {direction}")
    print(f"\n  → {'⚠️ 以下成分区分力不足，考虑删除或重构：' + '、'.join(weak) if weak else '✅ 各成分均有正向区分力'}")

    print("\n" + "=" * 74)
    print("总结")
    print("=" * 74)
    print(f"  {verdict_fill}")
    print(f"  {verdict_geo}")
    print(f"  跳空口径：每笔差 {delta:+.2f}pp")
    print(f"  真实可成交口径下的每笔期望 {e_real:+.2f}%、胜率 {w_real:.1%}（未计手续费/滑点）")
    if e_real < 0.5:
        print("\n  ⚠️ 修正后期望接近零 → 在此之前，回测产出的任何「正期望」结论都不可用；"
              "\n     也不足以支撑打分校准或参数调优（噪声会主导优化方向）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
