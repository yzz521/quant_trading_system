"""因子验证：IC/IR、分组单调性、分层与滚动前瞻样本外。

为什么需要它
------------
选股与打分链路上游有 9 个个股分因子、7 个机会分因子、组合分，以及标定后的置信度。
在这之前没有人回答过一个基本问题：**这些因子在历史上真的有区分力吗？**

经验上「权重拍脑袋 + 从不回验」是量化系统最常见的失效来源：某个因子长期 IC≈0 甚至
反向，却因为权重固定而持续污染排序，表现为「推荐的票怪怪的、盈利不高」。本模块
把「因子有没有用」变成可复现的量化结论。

三个层次
--------
1. **区分力**：IC（Spearman 秩相关）与 IR（IC 均值 / IC 标准差）。用秩相关而非
   皮尔逊，因为收益率厚尾、因子量纲不一，秩相关只看排序，稳健得多。
2. **形状**：分组（分位）平均收益是否单调。IC 只说明「排序有关系」，分组单调性
   说明「越靠前的组确实越赚」——这是能不能按因子排序选票的直接依据。
3. **稳健性**：滚动前瞻（walk-forward）切片，训练段有区分力、测试段是否还成立；
   以及按市场状态分层，看因子是否只在单一状态里有效。

设计约束
--------
* 只依赖 numpy / pandas（不引 scipy/statsmodels），纯离线、无 IO、无隐式随机。
* 所有跨期统计都**先按日横截面计算再跨期聚合**，避免不同日期的因子量纲/分布差异
  污染结果（横截面因子在时间上不可直接混合）。
* 与 ``calibration.py`` 一致：rolling 前瞻而非随机切分，训练集严格早于测试集。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 阈值（判定口径集中在此，便于审计与调整）
# --------------------------------------------------------------------------- #
_MIN_NAMES = 3            # 一个横截面上至少几只票才算一次有效 IC
_MIN_PERIODS = 8          # 至少几个有效期才有统计意义
_IC_WEAK = 0.02           # |IC 均值| 低于此视为无区分力
_IR_WEAK = 0.30           # |IR| 低于此视为不稳定
_IC_TOL = 0.02            # 滚动前瞻中「训练→测试 IC 同号」的容差

# --------------------------------------------------------------------------- #
# 分组单调性判定（口径事先固定，不许为了让结论通过而事后调整）
#
# 原口径要求「严格逐档单调」= 相邻分位收益全部同向（5 档 ⇒ 4 个相邻对零逆序）。
# 问题：分位均值本身有抽样噪声，要求零逆序会把「整体趋势很强但有一对小噪声」
# 的因子与「完全无信号」的因子一视同仁地判死。实测 risk_reward_1 的
# Spearman ρ=0.7（强排序能力）却因 1 对逆序被判「分组不单调」。
#
# 新口径（组合判据，整体严格度不降）：
#   逆序 0 对                  → 通过（等价原口径）
#   逆序 1 对 且 |ρ| ≥ 0.6     → 通过（准单调，用 ρ 补偿）
#   逆序 ≥ 2 对                → 不通过
# 注意 ρ 门槛取 0.6 而非常见的 0.5：既然放宽了「零逆序」，就在强度上要得更严。
# --------------------------------------------------------------------------- #
_MONO_MAX_INVERSIONS = 1   # 最多容忍几对相邻逆序
_MONO_RHO_MIN = 0.60       # 准单调时要求的 |Spearman ρ| 下限

# --------------------------------------------------------------------------- #
# 「无区分力」的显著性门槛（口径事先固定）
#
# 只凭 |IC| < _IC_WEAK 就判「无区分力」，后果是**把整个维度的权重清零**
# （见 factor_weights.VERDICT_MULTIPLIER["useless"] = 0.0）。
# 但 IC 均值本身是个估计量：19 个有效期、IC 标准差约 0.35 时，其标准误约 0.08，
# 于是 IC=0.017 与 IC=0 在统计上根本不可区分。
#
# 实测 `opportunity_score` 正是如此被判「无区分力」（IC=0.017、t=0.20）——
# 以 0.003 的余量决定「整个机会分维度退出排序」，是噪声在替我们做决策。
#
# 因此「无区分力」必须**同时**满足 |IC| < _IC_WEAK 与 |t| ≥ _IC_T_SIG
# （即在大样本下确认无效）；|IC| 很小但 t 不显著时，归入「不稳定」
# （×0.5 降权而非清零）—— 那才是「没测出来」的正确表达。
# --------------------------------------------------------------------------- #
_IC_T_SIG = 2.0            # |t| 显著性门槛（「无区分力」的必要条件之一）

DEFAULT_FACTORS: tuple[str, ...] = (
    "confidence",
    "opportunity_score",
    "stock_score",
    "risk_reward_1",
)


# --------------------------------------------------------------------------- #
# 秩工具
# --------------------------------------------------------------------------- #
def average_ranks(x: Sequence[float]) -> np.ndarray:
    """平均秩（1-based）。并列取平均秩，与 AUC 的并列处理口径一致。"""
    a = np.asarray(x, dtype=float).ravel()
    n = a.size
    ranks = np.empty(n, dtype=float)
    order = np.argsort(a, kind="mergesort")
    sx = a[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sx[j] == sx[i]:
            j += 1
        ranks[order[i:j]] = (i + j + 1) / 2.0
        i = j
    return ranks


def _clean_pair(factor: Sequence[float], ret: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(factor, dtype=float).ravel()
    r = np.asarray(ret, dtype=float).ravel()
    if f.size != r.size:
        raise ValueError("factor 与 return 长度不一致")
    mask = np.isfinite(f) & np.isfinite(r)
    return f[mask], r[mask]


def spearman_ic(
    factor: Sequence[float],
    ret: Sequence[float],
    *,
    min_names: int = _MIN_NAMES,
) -> float:
    """Spearman 秩相关系数（即 IC）。样本不足或因子为常数时返回 NaN。

    返回 NaN 而不是 0：0 表示「测过、无相关」，NaN 表示「测不了」——把两者混为
    一谈会让「无数据」被当成「已验证无区分力」，是审计中最常见的自欺。
    """
    f, r = _clean_pair(factor, ret)
    if f.size < max(2, int(min_names)):
        return float("nan")
    fr = average_ranks(f)
    rr = average_ranks(r)
    fs = fr - fr.mean()
    rs = rr - rr.mean()
    denom = float(np.sqrt((fs * fs).sum() * (rs * rs).sum()))
    if denom <= 0.0:                      # 因子或收益为常数 → 无排序信息
        return float("nan")
    return float((fs * rs).sum() / denom)


# --------------------------------------------------------------------------- #
# 面板
# --------------------------------------------------------------------------- #
def _date_series(panel: pd.DataFrame, date_col: str) -> Optional[pd.Series]:
    if date_col is None or date_col not in panel.columns:
        return None
    d = pd.to_datetime(panel[date_col], errors="coerce")
    return d


def _iter_periods(
    panel: pd.DataFrame, date_col: str
) -> Iterable[tuple[Any, pd.DataFrame]]:
    """按日期产出横截面。无日期列时把整块当作一个横截面（pooled）。"""
    if date_col is None or date_col not in panel.columns:
        yield None, panel
        return
    d = _date_series(panel, date_col)
    if d is None:
        yield None, panel
        return
    valid = d.notna()
    sub = panel.loc[valid].copy()
    sub["_date"] = d[valid]
    for key, g in sub.groupby("_date", sort=True):
        yield key, g


def ic_series(
    panel: pd.DataFrame,
    factor_col: str,
    return_col: str,
    *,
    date_col: str = "date",
    min_names: int = _MIN_NAMES,
) -> pd.Series:
    """逐期 IC 序列（index = 日期）。

    每个日期内部做一次横截面秩相关，再把这些 IC 当作独立观测去算均值/IR。
    """
    if panel is None or len(panel) == 0:
        return pd.Series(dtype=float)
    if factor_col not in panel.columns or return_col not in panel.columns:
        raise KeyError(f"面板缺少列：{factor_col!r} 或 {return_col!r}")
    out: dict[Any, float] = {}
    for key, g in _iter_periods(panel, date_col):
        ic = spearman_ic(g[factor_col], g[return_col], min_names=min_names)
        if np.isfinite(ic):
            out[key] = ic
    return pd.Series(out, dtype=float)


def ic_stats(ic: pd.Series) -> dict:
    """把 IC 序列汇总成 IC 均值、IR、t 值、胜率。"""
    arr = np.asarray(ic, dtype=float).ravel() if ic is not None else np.array([])
    a = arr[np.isfinite(arr)]
    n = int(a.size)
    if n == 0:
        return {
            "n_periods": 0, "ic_mean": None, "ic_std": None,
            "ir": None, "t_stat": None, "ic_win_rate": None,
        }
    mean = float(a.mean())
    std = float(a.std(ddof=1)) if n > 1 else 0.0
    ir = (mean / std) if std > 0 else None
    t = (mean / (std / np.sqrt(n))) if (std > 0 and n > 1) else None
    return {
        "n_periods": n,
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "ir": round(ir, 3) if ir is not None else None,
        "t_stat": round(t, 3) if t is not None else None,
        "ic_win_rate": round(float((a > 0).mean()), 3),
    }


def quantile_returns(
    panel: pd.DataFrame,
    factor_col: str,
    return_col: str,
    *,
    n_quantiles: int = 5,
    date_col: str = "date",
    min_names_per_period: Optional[int] = None,
) -> pd.DataFrame:
    """分位平均收益：先按日分组、再跨期等权平均。

    每个日期内按因子排序等分成 Q 组（按秩，天然处理并列），组内取收益均值；最后
    对每个分位在日期维度上取平均。返回列：quantile(1..Q)、mean_return、n_periods、
    n_names。分位 1 = 因子最低，分位 Q = 因子最高。
    """
    q = max(2, int(n_quantiles))
    need = int(min_names_per_period) if min_names_per_period else max(q, _MIN_NAMES)
    rows: list[dict] = []
    for key, g in _iter_periods(panel, date_col):
        f, r = _clean_pair(g[factor_col], g[return_col])
        if f.size < max(need, q):
            continue
        order = np.argsort(f, kind="mergesort")
        for qi, idx in enumerate(np.array_split(order, q), start=1):
            if idx.size == 0:
                continue
            rows.append({"date": key, "quantile": qi,
                         "ret": float(r[idx].mean()), "n": int(idx.size)})
    if not rows:
        return pd.DataFrame(columns=["quantile", "mean_return", "n_periods", "n_names"])
    df = pd.DataFrame(rows)
    agg = (
        df.groupby("quantile")
        .agg(mean_return=("ret", "mean"), n_periods=("ret", "size"), n_names=("n", "mean"))
        .reset_index()
    )
    agg["mean_return"] = agg["mean_return"].round(4)
    agg["n_names"] = agg["n_names"].round(2)
    return agg


def quantile_monotonicity(qret: pd.DataFrame) -> dict:
    """分位收益的单调性：秩相关 + 是否严格单调 + 逆序对数 + 顶底差。"""
    if qret is None or len(qret) < 2:
        return {"n_quantiles": 0, "spearman": None, "monotone_up": False,
                "monotone_down": False, "top_minus_bottom": None, "direction": 0,
                "n_inversions": None}
    qs = qret["quantile"].to_numpy(float)
    mr = qret["mean_return"].to_numpy(float)
    rho = spearman_ic(qs, mr, min_names=3)
    up = bool(np.all(np.diff(mr) >= 0))
    down = bool(np.all(np.diff(mr) <= 0))
    spread = float(mr[-1] - mr[0])
    direction = 0
    if np.isfinite(rho):
        direction = 1 if rho > 0 else (-1 if rho < 0 else 0)
    # 逆序对数：按 ρ 定的方向数「走反」的相邻对。方向不定时全部计为逆序
    # （等价于判不单调），避免给方向不明的因子白送通过。
    diff = np.diff(mr)
    if direction > 0:
        n_inv = int(np.sum(diff < 0))
    elif direction < 0:
        n_inv = int(np.sum(diff > 0))
    else:
        n_inv = int(len(diff))
    return {
        "n_quantiles": int(len(qret)),
        "spearman": None if not np.isfinite(rho) else round(float(rho), 3),
        "monotone_up": up,
        "monotone_down": down,
        "top_minus_bottom": round(spread, 4),
        "direction": direction,
        "n_inversions": n_inv,
    }


def _verdict(stats: dict, mono: dict, *, expected_sign: int = 1) -> str:
    """给一个因子下结论（人话，可直接进报告）。

    返回文案会被 ``factor_weights.classify_verdict`` 按**关键词**归类
    （「不单调」→ 降权、「可用」→ 全额保留），所以措辞里必须保留这些词。

    Args:
        expected_sign: 该因子被假定的方向。本系统的四个因子都按「越大越好」使用，
            故默认 ``+1``。方向校验见下。
    """
    if stats.get("n_periods", 0) < _MIN_PERIODS:
        return f"样本不足（仅 {stats.get('n_periods', 0)} 期，需 ≥{_MIN_PERIODS}）"
    ic = stats.get("ic_mean")
    if ic is None or abs(ic) < _IC_WEAK:
        t = stats.get("t_stat")
        # t 缺失 = 每期 IC 完全相同（ic_std=0）：IC 稳定地贴在 0 附近，
        # 那确实是「确认无区分力」，与「样本不足导致测不准」不是一回事。
        if t is None or abs(float(t)) >= _IC_T_SIG:
            return "无区分力（IC 接近 0），不建议参与排序"
        # IC≈0 但 t 不显著 → 与「未测出」不可区分，只降权不清零
        return (f"区分力不稳定（IC {ic} 接近 0，但 t={t} 未达显著门槛 "
                f"{_IC_T_SIG}，无法与「未测出」区分），单期噪声大")
    # ---- 方向校验：必须在 IR / 单调性之前 ----
    # 下游 factor_weights 只会**按正权重**使用这些因子。若 IC 均值为负，
    # 判「可用」是危险结论：那等于把排序倒过来用 —— 而且越"有效"越有害。
    # 「无信号」只是浪费权重，「反向信号」是主动选错票，两者不能同档处理。
    if expected_sign and ic * expected_sign < 0:
        t = stats.get("t_stat")
        if t is not None and abs(float(t)) >= _IC_T_SIG:
            return (f"方向相反且显著（IC 均值 {ic}，t={t}），"
                    f"按正权重使用会系统性选错票，应移出公式")
        return (f"方向存疑（IC 均值 {ic} 与「越大越好」相反，但 t={t} 未达显著门槛 "
                f"{_IC_T_SIG}，不足以确认反向），按「不稳定」降权处理")
    ir = stats.get("ir")
    if ir is None:
        # IC 标准差为 0（每期 IC 完全相同）→ 无波动可算 IR，但方向一致到极致，
        # 不能判为「不稳定」；标准差非 0 却算不出 IR 才是真的异常。
        if stats.get("ic_std") not in (None, 0.0):
            return "区分力不稳定（IR 无法计算）"
    elif abs(ir) < _IR_WEAK:
        return f"区分力不稳定（IR {ir}），单期噪声大"
    if not (mono["monotone_up"] or mono["monotone_down"]):
        n_inv = mono.get("n_inversions")
        rho = mono.get("spearman")
        if n_inv is None:
            return "分组不单调，排序含义模糊"
        if n_inv > _MONO_MAX_INVERSIONS:
            return (f"分组不单调（相邻逆序 {n_inv} 对 > 容忍 {_MONO_MAX_INVERSIONS} 对），"
                    f"排序含义模糊")
        if rho is None or abs(float(rho)) < _MONO_RHO_MIN:
            return (f"分组不单调（仅 {n_inv} 对逆序，但 ρ={rho} 未达补偿门槛 "
                    f"{_MONO_RHO_MIN}）")
        return (f"可用（IC/IR 达标；分组准单调：仅 {n_inv} 对相邻逆序，"
                f"ρ={rho} ≥ {_MONO_RHO_MIN} 补偿达标）")
    return "可用（IC/IR 与分组单调性均达标）"


def validate_factor(
    panel: pd.DataFrame,
    factor_col: str,
    return_col: str,
    *,
    n_quantiles: int = 5,
    date_col: str = "date",
    min_names: int = _MIN_NAMES,
    expected_sign: int = 1,
) -> dict:
    """单因子完整体检：IC/IR + 分位收益 + 单调性 + 结论。

    Args:
        expected_sign: 该因子被假定的方向（``+1`` = 越大越好）。为负时，
            IC 为负会被判为「方向相反」而不是「可用」。
    """
    ic = ic_series(panel, factor_col, return_col, date_col=date_col, min_names=min_names)
    stats = ic_stats(ic)
    qret = quantile_returns(panel, factor_col, return_col,
                            n_quantiles=n_quantiles, date_col=date_col)
    mono = quantile_monotonicity(qret)
    n_obs = 0
    if panel is not None and len(panel) and factor_col in panel.columns:
        n_obs = int(np.isfinite(pd.to_numeric(panel[factor_col], errors="coerce")).sum())
    return {
        "factor": factor_col,
        "n_obs": n_obs,
        "ic": stats,
        "quantiles": qret.to_dict("records"),
        "monotonicity": mono,
        "verdict": _verdict(stats, mono, expected_sign=expected_sign),
    }


def validate_factors(
    panel: pd.DataFrame,
    factor_cols: Optional[Sequence[str]] = None,
    *,
    return_col: str = "ret",
    n_quantiles: int = 5,
    date_col: str = "date",
    min_names: int = _MIN_NAMES,
) -> pd.DataFrame:
    """多因子批量体检，按 |IC 均值| 降序返回汇总表。

    ``factor_cols`` 省略时自动推断：取除 ``date``/``code``/收益列之外的**数值型**列。
    面板常带 ``decision`` / ``exit_reason`` / ``industry`` 等状态列（供
    ``stratified_ic`` 分层用），它们不是因子 —— 原实现会把它们也当因子送进
    ``np.asarray(..., dtype=float)``，直接抛 ``ValueError`` 让整轮验证失败。
    显式传入 ``factor_cols`` 时若含非数值列，则抛出明确的类型错误（不静默跳过，
    因为那时是调用方写错了列名）。
    """
    if factor_cols:
        cols = list(factor_cols)
        bad = [c for c in cols
               if c not in panel.columns or not pd.api.types.is_numeric_dtype(panel[c])]
        if bad:
            detail = "、".join(
                f"{c}({panel[c].dtype})" if c in panel.columns else f"{c}(不存在)"
                for c in bad
            )
            raise ValueError(f"factor_cols 含非数值型/不存在的列：{detail}")
    else:
        cols = [c for c in panel.columns
                if c not in (date_col, return_col, "code")
                and pd.api.types.is_numeric_dtype(panel[c])]
    rows = []
    for c in cols:
        rep = validate_factor(panel, c, return_col, n_quantiles=n_quantiles,
                              date_col=date_col, min_names=min_names)
        s, m = rep["ic"], rep["monotonicity"]
        rows.append({
            "factor": c,
            "n_obs": rep["n_obs"],
            "n_periods": s["n_periods"],
            "ic_mean": s["ic_mean"],
            "ir": s["ir"],
            "t_stat": s["t_stat"],
            "ic_win_rate": s["ic_win_rate"],
            "top_minus_bottom": m["top_minus_bottom"],
            "monotone": m["monotone_up"] or m["monotone_down"],
            "verdict": rep["verdict"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # ``ic_mean`` 可能是 ``None``（有效期数不足 / 因子恒定 → 测不了）。
    # 原实现直接 ``df["ic_mean"].abs()``，一旦有任何一个因子测不出 IC 就抛
    # ``TypeError: bad operand type for abs(): 'NoneType'``，整轮批量验证全废 ——
    # 恰恰是最需要「先看看哪些因子不行」的时候。这里把 None 排到最后。
    key = pd.to_numeric(df["ic_mean"], errors="coerce").abs().fillna(-1.0)
    return df.reindex(key.sort_values(ascending=False, kind="mergesort").index) \
             .reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 稳健性：滚动前瞻 + 分层
# --------------------------------------------------------------------------- #
def walk_forward_factor(
    panel: pd.DataFrame,
    factor_col: str,
    return_col: str,
    *,
    n_splits: int = 5,
    embargo_periods: int = 0,
    n_quantiles: int = 5,
    date_col: str = "date",
    min_names: int = _MIN_NAMES,
) -> pd.DataFrame:
    """滚动前瞻切片：训练段（更早）→ 测试段（更晚），检查 IC 是否延续。

    返回每个 fold 一行：train_ic / test_ic / train_ir / test_ir / sign_kept。
    ``sign_kept`` 为 True 表示测试段 IC 与训练段同号且幅度未衰减到容差以下 ——
    这是「因子样本外仍成立」的最低要求。

    无日期列时退化为按行顺序切块（仍严格保证训练在测试之前）。
    """
    if panel is None or len(panel) == 0:
        return pd.DataFrame(columns=["fold", "train_periods", "test_periods",
                                     "train_ic", "test_ic", "train_ir", "test_ir",
                                     "train_end", "test_start", "sign_kept"])
    dated = bool(date_col) and date_col in panel.columns
    if dated:
        d = _date_series(panel, date_col)
        ordered = panel.loc[d.notna()].copy()
        ordered["_date"] = d[d.notna()]
        ordered = ordered.sort_values("_date", kind="mergesort")
        keys = np.array(sorted(ordered["_date"].unique()))
        blocks = np.array_split(keys, max(2, int(n_splits)))
        def _sub(dates: np.ndarray) -> pd.DataFrame:
            return ordered[ordered["_date"].isin(dates)]
    else:
        ordered = panel.reset_index(drop=True)
        idx = np.arange(len(ordered))
        blocks = np.array_split(idx, max(2, int(n_splits)))
        def _sub(rows: np.ndarray) -> pd.DataFrame:
            return ordered.iloc[rows]

    rows = []
    for i in range(1, len(blocks)):
        train_dates = np.concatenate(blocks[:i])
        if embargo_periods > 0:
            train_dates = train_dates[: max(0, train_dates.size - int(embargo_periods))]
        if train_dates.size == 0 or blocks[i].size == 0:
            continue
        tr = _sub(train_dates)
        te = _sub(blocks[i])
        tr_stats = ic_stats(ic_series(tr, factor_col, return_col,
                                      date_col=date_col, min_names=min_names))
        te_stats = ic_stats(ic_series(te, factor_col, return_col,
                                      date_col=date_col, min_names=min_names))
        ti, si = tr_stats["ic_mean"], te_stats["ic_mean"]
        kept = (
            ti is not None and si is not None
            and np.sign(ti) == np.sign(si)
            and abs(si) >= abs(ti) * 0.5 - _IC_TOL
        )
        rows.append({
            "fold": i,
            "train_periods": tr_stats["n_periods"],
            "test_periods": te_stats["n_periods"],
            "train_ic": ti,
            "test_ic": si,
            "train_ir": tr_stats["ir"],
            "test_ir": te_stats["ir"],
            "train_end": tr["_date"].max() if dated and len(tr) else None,
            "test_start": te["_date"].min() if dated and len(te) else None,
            "sign_kept": bool(kept),
        })
    return pd.DataFrame(rows)


def stratified_ic(
    panel: pd.DataFrame,
    factor_col: str,
    return_col: str,
    regime_col: str,
    *,
    date_col: str = "date",
    min_names: int = _MIN_NAMES,
) -> pd.DataFrame:
    """按状态列（如市场状态、行业、市值档）分层算 IC。

    用途：一个因子若只在「牛市」有效，全样本 IC 会被高估，分层能暴露这一点。
    """
    if panel is None or len(panel) == 0 or regime_col not in panel.columns:
        return pd.DataFrame(columns=["regime", "n_obs", "n_periods",
                                     "ic_mean", "ir", "t_stat", "ic_win_rate"])
    rows = []
    for key, g in panel.groupby(regime_col, sort=True, dropna=True):
        stats = ic_stats(ic_series(g, factor_col, return_col,
                                   date_col=date_col, min_names=min_names))
        rows.append({"regime": key, "n_obs": int(len(g)), **stats})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 从回测记录构造面板
# --------------------------------------------------------------------------- #
def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _is_executed(v: Any) -> bool:
    """``entry_executed`` 是否**明确**表示已成交。

    只有明确的 ``True`` / ``1`` / ``"true"`` 才算成交。原实现写的是
    ``if require_executed and _attr(t, "entry_executed") is False: continue`` ——
    只在**恰好等于 False** 时跳过，于是字段缺失（旧格式记录、手工拼的 dict、
    ``None``）会被当成「已成交」混进面板，把「没买」记成「买了」，
    污染 IC、分组收益与标定样本。宁可漏（保守）也不能错（乐观）。
    """
    if v is True:
        return True
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) == 1.0
    return False


def build_factor_panel(
    trades: Iterable[Any],
    *,
    factors: Sequence[str] = DEFAULT_FACTORS,
    return_attr: str = "return_pct",
    return_col: str = "ret",
    date_col: str = "date",
    code_col: str = "code",
    extra_cols: Sequence[str] = (),
    require_executed: bool = True,
) -> pd.DataFrame:
    """把回测交易记录转成因子验证面板。

    ``return_attr`` 是交易记录上的收益字段名（回测默认 ``return_pct``），
    ``return_col`` 是面板里的列名（默认 ``ret``，与 ``validate_factor`` 的默认一致）。
    ``extra_cols`` 用于透传状态列（如 ``decision``、``exit_reason``），供
    ``stratified_ic`` 做分层分析。

    只保留**成交**的交易：未成交（价格从未进区间）的收益没有意义，混进来会把
    「没买」当成「没赚」稀释掉因子的区分力。``require_executed=True`` 时要求
    ``entry_executed`` **明确为真** —— 字段缺失不算成交（见 ``_is_executed``）。
    """
    rows: list[dict] = []
    for t in trades or []:
        if require_executed and not _is_executed(_attr(t, "entry_executed")):
            continue
        ret = _attr(t, return_attr)
        date = _attr(t, date_col)
        if ret is None or date is None:
            continue
        try:
            ret = float(ret)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(ret):
            continue
        row: dict[str, Any] = {date_col: date, code_col: _attr(t, code_col) or ""}
        has_factor = False
        for f in factors:
            v = _attr(t, f)
            row[f] = float(v) if isinstance(v, (int, float)) and np.isfinite(v) else v
            if v is not None:
                has_factor = True
        if not has_factor:
            continue
        for c in extra_cols:
            row[c] = _attr(t, c)
        row[return_col] = ret
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[date_col, code_col, *factors, *extra_cols, return_col])
    df = pd.DataFrame(rows)
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.loc[df[date_col].notna()].reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# 人类可读摘要
# --------------------------------------------------------------------------- #
def summarize(report: dict) -> str:
    """把 validate_factor 的结果渲染成一段可读文本（报告 / 看板复用）。"""
    f = report["factor"]
    s = report["ic"]
    m = report["monotonicity"]
    lines = [
        f"因子 {f}：{report['verdict']}",
        f"  IC 均值 {s['ic_mean']}   IR {s['ir']}   t {s['t_stat']}   "
        f"IC>0 占比 {s['ic_win_rate']}   有效期数 {s['n_periods']}   样本 {report['n_obs']}",
    ]
    qs = report.get("quantiles") or []
    if qs:
        parts = [f"Q{r['quantile']}={r['mean_return']:.3f}" for r in qs]
        lines.append("  分位平均收益：" + "  ".join(parts)
                     + f"   顶底差 {m['top_minus_bottom']}")
    return "\n".join(lines)
