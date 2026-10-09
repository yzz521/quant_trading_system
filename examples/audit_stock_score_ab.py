"""审计：裁决 2「打分被系统性压低」的严格 A/B 对照。

背景
----
2026-10 的裁决 2 把 ``stock_score`` 的基本面维度从「市值/换手硬扣分」改成「只由 ROE 驱动」，
并新增「缺失 ≠ 中性」的重归一化。当时报告写「均值 51.7 → 59.4、≥60 从 4/40 → 15/40」。

问题：那两个「旧」数字是拿**两次不同时间的运行**对照出来的 —— 两次之间行情、候选名单、
财务快照全变了（「快照漂移」），差值里混着市场变化，不能归因到代码改动上。

本脚本的做法
------------
把**同一批** 40 只票的 ``(K线, extra)`` 当场截获下来，再分别喂给旧实现与新实现，
从而把「代码改动」与「市场变化」彻底分离。

用法
----
    PYTHONPATH=/Users/yzz/workspace/gp .venv/bin/python examples/audit_stock_score_ab.py

需要联网（抓 40 只票的行情 + 基本面），约 30 秒。

结论（2026-10-08 实测，40 只 / 同日）
------------------------------------
    ① 旧基本面公式 + 不重归一化   均值 54.7  ≥60 10/40   （＝真·旧实现）
    ② 旧基本面公式 + 重归一化     均值 59.4  ≥60 15/40
    ③ 新基本面公式 + 不重归一化   均值 55.9  ≥60 12/40
    ④ 新基本面公式 + 重归一化     均值 59.4  ≥60 15/40   （＝真·新实现）

    ⇒ ② 与 ④ 完全相同 ⇒ **分数抬升 100% 来自「重归一化」**。
      基本面公式重写对总分贡献为 0，因为 ``extra`` 里 ``roe`` 覆盖率为 0/40（数据源不提供）
      ⇒ ``fundamental`` 被整维剔除 ⇒ 它算多少都不进总分。
      基本面公式重写的价值是**语义正确性**（删掉市值/换手这个错误代理），不是分数。
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, "/Users/yzz/workspace/gp")

from quant_trading_system.stock_analysis.opportunity import OpportunityEngine  # noqa: E402
from quant_trading_system.stock_analysis.scoring import stock_score as S  # noqa: E402

# ---------------------------------------------------------------- 1) 截获输入
CAP: list = []
_ORIG_ANALYZE = OpportunityEngine.analyze


def _spy(self, code, name, df, extra=None, **kw):
    """把每只票的 (df, extra, regime_score) 截下来，再走原逻辑。"""
    CAP.append((code, name, df, dict(extra or {}), getattr(self, "regime_score", None)))
    return _ORIG_ANALYZE(self, code, name, df, extra=extra, **kw)


def capture(top_n: int = 40) -> None:
    """跑一次真实管线，截获输入。"""
    from quant_trading_system.stock_analysis.opportunity import OpportunityBatchScanner
    from quant_trading_system.stock_analysis.screener import screen_candidates
    from quant_trading_system.stock_analysis.sector import get_stock_sectors

    OpportunityEngine.analyze = _spy
    try:
        try:
            sector_map = get_stock_sectors()
        except Exception:
            sector_map = {}
        cands = screen_candidates("CN", top_n=top_n, industry_map=sector_map)
        OpportunityBatchScanner(fetch_fundamentals=True, workers=5).scan(cands, market="CN")
    finally:
        OpportunityEngine.analyze = _ORIG_ANALYZE


# ---------------------------------------------------------------- 2) 旧实现
def old_fundamental(df, extra: dict) -> float:
    """HEAD 版本的基本面打分（市值 invert 硬扣 10 分 + 换手扣分 + ROE 加分）。

    与 ``git show HEAD:stock_analysis/scoring/stock_score.py`` 中的实现逐行一致。
    """
    s = 50.0
    n = 0
    cap = S._num(extra.get("total_cap_yi"))
    if cap:
        s += S.normalize_component(cap, 10, 300, invert=True) * 0.4 - 10
        n += 1
    turnover = S._num(extra.get("turnover"))
    if turnover is not None:
        s += (S.normalize_component(turnover, 0.3, 8.0) - 50) * 0.3
        n += 1
    roe = S._num(extra.get("roe"))
    if roe is not None:
        s += (S.normalize_component(roe, 0, 20) - 50) * 0.5
        n += 1
    if n == 0:  # 无财务数据：用日均成交额兜底
        import pandas as pd

        if df is not None and "amount" in df.columns and len(df) > 0:
            amt = pd.to_numeric(df["amount"], errors="coerce").tail(20).mean()
            if amt is not None and not np.isnan(amt):
                s = S.normalize_component(float(amt), 5e7, 2e9)
    return float(np.clip(s, 0, 100))


# ---------------------------------------------------------------- 3) 打分
def score_one(df, extra: dict, regime, *, new_fundamental: bool, renormalize: bool):
    """按指定开关算一次总分，并返回被剔除的维度。"""
    fund = S._score_fundamental(df, extra) if new_fundamental else old_fundamental(df, extra)
    comps = {
        "fundamental": fund,
        "growth": S._score_growth(extra),
        "technical": S.score_trend(df),
        "momentum": S.score_momentum(df),
        "capital_flow": S._score_capital_flow(df, extra),
        "valuation": S._score_valuation(df, extra),
        "market_env": S._score_market_env(regime),
        "sector": S._score_sector(None),
        "risk": S._score_risk(df, None, None),
    }
    weights = dict(S.WEIGHTS)
    gated: list = []
    if renormalize:
        gated = [k for k in weights
                 if k in S._DATA_GATED_DIMS and not S._dim_has_data(k, extra, regime, None)]
        active = {k: v for k, v in weights.items() if k not in gated}
        if gated and len(active) >= S._MIN_ACTIVE_DIMS and sum(active.values()) > 0:
            scale = sum(weights.values()) / sum(active.values())
            weights = {k: (v * scale if k in active else 0.0) for k, v in weights.items()}
    return sum(comps[k] * weights[k] for k in weights), gated


def _stat(label: str, xs) -> None:
    arr = np.asarray(xs)
    print(f"  {label:<34} 均值={arr.mean():6.1f}  max={arr.max():6.1f}  "
          f"≥60={int((arr >= 60).sum()):>2}/{len(arr)}  ≥55={int((arr >= 55).sum()):>2}/{len(arr)}")


def main() -> None:
    capture()
    n = len(CAP)
    print(f"\n截获 {n} 只票的输入")
    print(f"真实 regime_score 取值分布: {Counter(c[4] for c in CAP)}\n")

    # extra 字段覆盖度 —— 解释「为什么 fundamental 会被剔除」
    print(f"=== extra 字段覆盖（共 {n} 只）===")
    for key in ("roe", "main_net", "pe", "total_cap_yi", "turnover",
                "rev_yoy", "profit_yoy", "amount"):
        hit = sum(1 for _c, _nm, _df, ex, _rg in CAP
                  if ex.get(key) is not None
                  and not (isinstance(ex.get(key), float) and np.isnan(ex.get(key))))
        print(f"  {key:<14} {hit:>2}/{n}")

    print("\n=== 严格 A/B（同一批输入，口径与真实管线一致）===")
    combos = [
        ("① 旧基本面 + 不重归一化", False, False),
        ("② 旧基本面 + 重归一化", False, True),
        ("③ 新基本面 + 不重归一化", True, False),
        ("④ 新基本面 + 重归一化", True, True),
    ]
    results: dict = {}
    for label, new_f, renorm in combos:
        vals, gates = [], Counter()
        for _c, _nm, df, ex, rg in CAP:
            v, g = score_one(df, ex, rg, new_fundamental=new_f, renormalize=renorm)
            vals.append(v)
            gates[tuple(sorted(g))] += 1
        results[label] = np.asarray(vals)
        _stat(label, vals)
        print(f"       剔除组合: {dict(gates)}")

    old = results["① 旧基本面 + 不重归一化"]
    new = results["④ 新基本面 + 重归一化"]
    delta = new - old
    print("\n=== 真·旧 vs 真·新（逐票差值）===")
    print(f"  中位={np.median(delta):+.1f}  均值={delta.mean():+.1f}  "
          f"最小={delta.min():+.1f}  最大={delta.max():+.1f}")
    print(f"  上升={int((delta > 0.05).sum())}/{n}  下降={int((delta < -0.05).sum())}/{n}  "
          f"不变={int((np.abs(delta) <= 0.05).sum())}/{n}")
    print(f"  跨过 60 门槛（旧<60≤新）={int(((old < 60) & (new >= 60)).sum())} 只")
    print("\n  注：② 与 ④ 若完全相同，则抬升全部来自「重归一化」，基本面公式重写对总分贡献为 0。")


if __name__ == "__main__":
    main()
