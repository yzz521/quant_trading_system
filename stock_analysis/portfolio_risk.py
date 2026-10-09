"""组合级风控：集中度、行业暴露、相关性、VaR/CVaR 与回撤熔断。

为什么需要它
------------
单票风控（``position_sizing``）只回答「这一笔最多买多少」，回答不了「**合起来**的
风险有多大」。真实亏损几乎都来自组合层面：

* 十只票里有六只是同一个行业 —— 单票各 10% 看着分散，实际是一个 60% 的赌注；
* 持仓之间高度相关（同一题材、同涨同跌）—— 名义上分散，风险上没分散；
* 连续回撤时继续按原仓位开新仓 —— 亏损被放大，而不是被刹车。

本模块把这些做成可计算的指标与明确的限值，输出「哪条超限、该怎么调」。

设计约束
--------
* 只依赖 numpy / pandas（不引 scipy）：正态分位用 Acklam 有理逼近实现。
* 纯函数、无 IO、无隐式随机，便于进 CI 与审计。
* 所有比例都用**占总资产的比例**（0~1），与 ``position_sizing.max_position_pct``
  口径一致，避免「%」与「小数」两套单位混用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:                      # 仅供类型注解；运行时保持惰性导入（本模块要轻）
    from pathlib import Path


# --------------------------------------------------------------------------- #
# 限值
# --------------------------------------------------------------------------- #
@dataclass
class RiskLimits:
    """组合风控限值。默认值偏保守，按账户风险偏好调整。"""

    max_single_pct: float = 0.20        # 单票上限（与 position_sizing 一致）
    max_industry_pct: float = 0.35      # 单一行业上限
    max_top3_pct: float = 0.60          # 前三大持仓合计上限
    max_avg_corr: float = 0.70          # 平均两两相关系数上限
    var_confidence: float = 0.95        # VaR 置信度
    max_var_pct: float = 0.03           # 单日 VaR 上限（占总资产）
    max_drawdown_pct: float = 0.20      # 触发完全停止开新仓的回撤
    dd_levels: tuple[tuple[float, float], ...] = (
        (-0.05, 0.75),
        (-0.10, 0.50),
        (-0.15, 0.25),
        (-0.20, 0.00),
    )

    def to_dict(self) -> dict:
        return {
            "max_single_pct": self.max_single_pct,
            "max_industry_pct": self.max_industry_pct,
            "max_top3_pct": self.max_top3_pct,
            "max_avg_corr": self.max_avg_corr,
            "var_confidence": self.var_confidence,
            "max_var_pct": self.max_var_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "dd_levels": [list(x) for x in self.dd_levels],
        }


# --------------------------------------------------------------------------- #
# 正态分位（Acklam 有理逼近，误差 < 1.15e-9）
# --------------------------------------------------------------------------- #
def norm_ppf(p: float) -> float:
    """标准正态分位函数。scipy 不在依赖里，这里自备。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p 必须落在 (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = float(np.sqrt(-2.0 * np.log(p)))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = float(np.sqrt(-2.0 * np.log(1.0 - p)))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


# --------------------------------------------------------------------------- #
# 持仓归一化
# --------------------------------------------------------------------------- #
def _as_dict(h: Any) -> dict:
    if isinstance(h, dict):
        return dict(h)
    if hasattr(h, "to_dict"):
        try:
            return dict(h.to_dict())
        except Exception:  # noqa: BLE001
            pass
    return {k: v for k, v in vars(h).items() if not k.startswith("_")}


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def holdings_frame(holdings: Iterable[Any]) -> pd.DataFrame:
    """把持仓列表归一化成 ``code / name / industry / market_value / weight``。

    ``weight`` 优先取显式值；缺失时按 ``market_value`` 归一化。两者都缺失的行被丢弃
    （没有权重就没有组合风险可谈）。
    """
    rows: list[dict] = []
    for h in holdings or []:
        d = _as_dict(h)
        code = str(d.get("code") or "").strip()
        if not code:
            continue
        qty = _num(d.get("quantity") or d.get("shares"))
        price = _num(d.get("current_price") or d.get("price") or d.get("last"))
        mv = _num(d.get("market_value"))
        if mv is None and qty is not None and price is not None:
            mv = qty * price
        rows.append({
            "code": code,
            "name": str(d.get("name") or code),
            "industry": str(d.get("industry") or d.get("sector") or "未知"),
            "market": str(d.get("market") or ""),
            "market_value": mv,
            # 只认 0~1 的比例口径：不接收 weight_pct 之类的百分数别名，
            # 避免「50」被当成 5000% 这种静默单位错误。
            "weight": _num(d.get("weight")),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["code", "name", "industry", "market",
                                     "market_value", "weight"])
    if df["weight"].notna().any():
        df["weight"] = df["weight"].astype(float)
    else:
        df["weight"] = np.nan
    # 权重缺失（或合计为 0）时用市值补
    tot = float(df["weight"].fillna(0.0).sum())
    if tot <= 0 and df["market_value"].notna().any():
        mv_tot = float(df["market_value"].fillna(0.0).sum())
        if mv_tot > 0:
            df["weight"] = df["market_value"].fillna(0.0) / mv_tot
    df = df.loc[df["weight"].notna() & (df["weight"] > 0)].reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# 集中度与行业暴露
# --------------------------------------------------------------------------- #
def concentration(weights: Sequence[float]) -> dict:
    """集中度：单票最大值、前三大合计、HHI 与有效持仓数。"""
    w = np.asarray(weights, dtype=float).ravel()
    w = w[np.isfinite(w) & (w > 0)]
    if w.size == 0:
        return {"n": 0, "top1": None, "top3": None, "hhi": None, "effective_n": None}
    w = np.sort(w)[::-1]
    hhi = float((w ** 2).sum())
    return {
        "n": int(w.size),
        "top1": round(float(w[0]), 4),
        "top3": round(float(w[:3].sum()), 4),
        "hhi": round(hhi, 4),
        "effective_n": round(1.0 / hhi, 2) if hhi > 0 else None,
    }


def industry_exposure(
    frame: pd.DataFrame, limits: Optional[RiskLimits] = None
) -> pd.DataFrame:
    """行业暴露：各行业权重、只数、是否超限。"""
    lim = limits or RiskLimits()
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["industry", "weight", "n", "over_limit"])
    g = (frame.groupby("industry", sort=False)
         .agg(weight=("weight", "sum"), n=("code", "size"))
         .reset_index()
         .sort_values("weight", ascending=False)
         .reset_index(drop=True))
    g["weight"] = g["weight"].round(4)
    g["over_limit"] = g["weight"] > lim.max_industry_pct + 1e-9
    return g


# --------------------------------------------------------------------------- #
# 相关性与组合波动
# --------------------------------------------------------------------------- #
def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    """日收益率的皮尔逊相关矩阵（成对完整观测）。"""
    if returns is None or returns.empty:
        return pd.DataFrame()
    r = returns.apply(pd.to_numeric, errors="coerce")
    r = r.loc[:, r.notna().sum() >= 2]
    if r.shape[1] < 2:
        return pd.DataFrame()
    return r.corr(min_periods=2)


def average_pairwise_correlation(corr: pd.DataFrame) -> Optional[float]:
    """平均两两相关系数（上三角，不含对角线）。<2 只票时无法计算。"""
    if corr is None or corr.empty or corr.shape[0] < 2:
        return None
    vals = corr.to_numpy(dtype=float)
    iu = np.triu_indices_from(vals, k=1)
    a = vals[iu]
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return round(float(a.mean()), 4)


def portfolio_volatility(
    weights: Sequence[float], cov: np.ndarray, *, horizon: int = 1
) -> Optional[float]:
    """组合波动率（比例口径，按 ``sqrt(horizon)`` 缩放）。"""
    w = np.asarray(weights, dtype=float).ravel()
    c = np.asarray(cov, dtype=float)
    if w.size == 0 or c.shape != (w.size, w.size):
        return None
    var = float(w @ c @ w)
    if not np.isfinite(var) or var < 0:
        return None
    return float(np.sqrt(var) * np.sqrt(max(1, int(horizon))))


def parametric_var_cvar(
    weights: Sequence[float],
    cov: np.ndarray,
    *,
    confidence: float = 0.95,
    horizon: int = 1,
) -> dict:
    """正态假设下的参数化 VaR / CVaR（返回正数=潜在损失比例）。

    VaR = z·σ·√h；CVaR = σ·√h·φ(z)/(1−confidence)。
    """
    sigma = portfolio_volatility(weights, cov, horizon=horizon)
    if sigma is None:
        return {"var": None, "cvar": None, "sigma": None}
    z = norm_ppf(confidence)
    phi = float(np.exp(-0.5 * z * z) / np.sqrt(2.0 * np.pi))
    cvar = sigma * phi / max(1e-12, (1.0 - confidence))
    return {
        "var": round(float(z * sigma), 6),
        "cvar": round(float(cvar), 6),
        "sigma": round(float(sigma), 6),
    }


def historical_var_cvar(
    returns: Sequence[float], *, confidence: float = 0.95
) -> dict:
    """历史模拟法 VaR / CVaR（用实际分布，不假设正态）。"""
    r = np.asarray(returns, dtype=float).ravel()
    r = r[np.isfinite(r)]
    if r.size < 20:
        return {"var": None, "cvar": None, "n": int(r.size)}
    q = float(np.quantile(r, 1.0 - confidence))
    tail = r[r <= q]
    return {
        "var": round(float(-q), 6),
        "cvar": round(float(-tail.mean()) if tail.size else float(-q), 6),
        "n": int(r.size),
    }


_MIN_OBS = 20       # 计算协方差/相关性所需的最少观测数

# 回撤熔断所需的最少净值点数（≈1 个交易月）。
# 低于此数时熔断**仍然生效**（回撤是真实的，关掉更不安全），但结果会带上
# 「依据偏薄」的标注 —— 见 ``drawdown_brake`` / ``equity_curve_status``。
MIN_EQUITY_POINTS = 20


def _align_returns(returns: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """只保留有效观测 ≥ ``_MIN_OBS`` 的列，并转成数值。"""
    r = returns.apply(pd.to_numeric, errors="coerce")
    cols = [c for c in r.columns if r[c].notna().sum() >= _MIN_OBS]
    return r[cols], cols


def _align_weights(
    weights: Sequence[float],
    weight_index: Sequence[Any],
    keep_cols: Sequence[Any],
) -> Optional[np.ndarray]:
    """把权重按代码对齐到保留列，并在子集内重新归一化。

    ``weight_index`` 是权重自身的代码顺序（通常 = 持仓代码）；``keep_cols`` 是收益
    面板里有效列。两者不一致时（例如某只票没有足够历史收益）取交集并在交集内归一，
    避免「权重 5 只、收益 3 列」这种长度错配。
    """
    w = pd.Series(np.asarray(weights, dtype=float).ravel(), index=list(weight_index))
    w = w.reindex(list(keep_cols)).fillna(0.0).to_numpy(dtype=float)
    total = float(w.sum())
    if total <= 0:
        return None
    return w / total


def cov_from_returns(
    returns: pd.DataFrame,
    weights: Sequence[float],
    *,
    weight_index: Optional[Sequence[Any]] = None,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """从收益率面板构造协方差矩阵，并对齐权重（只保留有数据的列）。

    ``weight_index`` 缺省时认为 ``weights`` 的顺序与 ``returns.columns`` 一致。
    返回 ``(cov, aligned_weights)``；数据不足时返回 ``(None, None)``。
    """
    if returns is None or returns.empty:
        return None, None
    r, cols = _align_returns(returns)
    if len(cols) < 2:
        return None, None
    idx = returns.columns if weight_index is None else weight_index
    w = _align_weights(weights, idx, cols)
    if w is None:
        return None, None
    cov = r.cov(min_periods=_MIN_OBS).to_numpy(dtype=float)
    if not np.all(np.isfinite(cov)):
        cov = np.nan_to_num(cov, nan=0.0)
    return cov, w


def portfolio_return_series(
    returns: pd.DataFrame,
    weights: Sequence[float],
    *,
    weight_index: Optional[Sequence[Any]] = None,
) -> Optional[pd.Series]:
    """按权重合成的组合日收益序列（用于历史模拟法 VaR/CVaR）。

    缺失收益按 0 处理（当日停牌/未交易视为无盈亏），避免整行被丢弃。
    """
    if returns is None or returns.empty:
        return None
    r, cols = _align_returns(returns)
    if not cols:
        return None
    idx = returns.columns if weight_index is None else weight_index
    w = _align_weights(weights, idx, cols)
    if w is None:
        return None
    return pd.Series(r.fillna(0.0).to_numpy(dtype=float) @ w, index=r.index)


# --------------------------------------------------------------------------- #
# 回撤熔断
# --------------------------------------------------------------------------- #
def drawdown_series(equity: Sequence[float]) -> np.ndarray:
    """相对历史高点的回撤序列（<=0）。"""
    a = np.asarray(equity, dtype=float).ravel()
    a = a[np.isfinite(a)]
    if a.size == 0:
        return np.array([])
    peak = np.maximum.accumulate(a)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, a / peak - 1.0, 0.0)
    return dd


def max_drawdown(equity: Sequence[float]) -> Optional[float]:
    dd = drawdown_series(equity)
    return None if dd.size == 0 else round(float(dd.min()), 4)


def drawdown_brake(
    equity: Sequence[float], limits: Optional[RiskLimits] = None
) -> dict:
    """回撤熔断：按当前回撤给出**总仓位系数**（0~1）。

    系数用于缩放所有新开仓的建议仓位：回撤越深，开新仓越少，直到完全停止。

    同时返回**证据充分度**（``n_points`` / ``required`` / ``armed``）：
    净值曲线只有 2~3 个点时，回撤几乎完全由「最早那天恰好是高点」决定，
    算出来的数字看着精确、其实很薄。这里**不因此关闭熔断**（关掉是更不安全的
    做法 —— 回撤是真实的），而是把它标出来，让看板与邮件能说清「这个判断
    建立在几个点上」，而不是让沉默被误读成「已检查且正常」。
    """
    lim = limits or RiskLimits()
    vals = [v for v in np.asarray(equity, dtype=float).ravel() if np.isfinite(v)] \
        if equity is not None else []
    n_points = len(vals)
    armed = n_points >= MIN_EQUITY_POINTS
    base = {"n_points": n_points, "required": MIN_EQUITY_POINTS, "armed": armed}
    dd = drawdown_series(equity)
    if dd.size == 0:
        return {**base, "current_drawdown": None, "max_drawdown": None, "brake": 1.0,
                "level": "无净值数据 → 熔断未启用（系数 100%）", "triggered": False}
    cur = float(dd[-1])
    brake, level = 1.0, "正常"
    for thr, scale in sorted(lim.dd_levels, key=lambda x: x[0], reverse=True):
        if cur <= thr:
            brake, level = float(scale), f"回撤 {cur:.1%} → 仓位系数 {scale:.0%}"
    if not armed:
        level += (f"（净值曲线仅 {n_points} 个交易日，需 ≥{MIN_EQUITY_POINTS} 个"
                  f"才足够可靠 —— 判断依据偏薄，请谨慎解读）")
    return {
        **base,
        "current_drawdown": round(cur, 4),
        "max_drawdown": round(float(dd.min()), 4),
        "brake": round(brake, 4),
        "level": level,
        "triggered": brake < 1.0,
    }


def equity_curve_status(
    equity: Optional[Sequence[float]] = None,
    *,
    records: Optional[Sequence[dict]] = None,
    required: int = MIN_EQUITY_POINTS,
) -> dict:
    """净值曲线充分度：够不够支撑回撤熔断的可靠判断。

    ``equity`` 与 ``records`` 二选一（``records`` 为 ``load_equity_history`` 的返回）。
    用于在看板 / 邮件里说明「熔断为什么还没真正起作用」—— 曲线要按交易日累积，
    前 N 天它必然偏薄，这属于系统固有阶段，应当被明说而不是静默。
    """
    if equity is None and records is not None:
        equity = equity_values(records)
    vals = [v for v in np.asarray(equity if equity is not None else [],
                                  dtype=float).ravel() if np.isfinite(v)]
    n = len(vals)
    armed = n >= int(required)
    if n == 0:
        note = "尚无净值历史 → 回撤熔断未启用（按交易日自动累积）。"
    elif not armed:
        note = (f"净值曲线仅 {n} 个交易日（需 ≥{required}）→ 回撤判断依据偏薄；"
                f"曲线按交易日自动累积，满 {required} 个交易日后判断才可靠。")
    else:
        note = f"净值曲线 {n} 个交易日，回撤熔断已具备可靠判断依据。"
    return {"n_points": n, "required": int(required), "armed": armed, "note": note}



# --------------------------------------------------------------------------- #
# 汇总评估
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioRiskReport:
    """组合风控结论。"""

    n_holdings: int = 0
    total_weight: float = 0.0
    concentration: dict = field(default_factory=dict)
    by_industry: list = field(default_factory=list)
    correlation: dict = field(default_factory=dict)
    var: dict = field(default_factory=dict)
    drawdown: dict = field(default_factory=dict)
    breaches: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    verdict: str = "无持仓"
    limits: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n_holdings": self.n_holdings,
            "total_weight": self.total_weight,
            "concentration": self.concentration,
            "by_industry": self.by_industry,
            "correlation": self.correlation,
            "var": self.var,
            "drawdown": self.drawdown,
            "breaches": self.breaches,
            "actions": self.actions,
            "verdict": self.verdict,
            "limits": self.limits,
        }


def _verdict(breaches: list[str], drawdown: dict) -> str:
    if not breaches and not drawdown.get("triggered"):
        return "正常"
    if drawdown.get("brake", 1.0) <= 0.0:
        return "严重：回撤触及上限，应停止开新仓并降低总仓位"
    if len(breaches) >= 2:
        return "超限：多项风险指标越界，需调整后再开新仓"
    return "注意：单项风险指标越界"


def assess_portfolio_risk(
    holdings: Iterable[Any],
    *,
    returns: Optional[pd.DataFrame] = None,
    equity: Optional[Sequence[float]] = None,
    limits: Optional[RiskLimits] = None,
) -> PortfolioRiskReport:
    """组合风险体检：集中度 + 行业暴露 + 相关性 + VaR/CVaR + 回撤熔断。

    Args:
        holdings: 持仓（dict 或对象），需含 ``code`` 与 ``weight``/``market_value``。
        returns: 各持仓的日收益率面板（列 = 代码），用于相关性与参数化 VaR。
        equity: 账户净值序列（旧→新），用于回撤熔断。
        limits: 风控限值。
    """
    lim = limits or RiskLimits()
    frame = holdings_frame(holdings)
    if frame.empty:
        return PortfolioRiskReport(limits=lim.to_dict())

    w = frame["weight"].to_numpy(dtype=float)
    conc = concentration(w)
    ind = industry_exposure(frame, lim)
    total_w = round(float(w.sum()), 4)

    corr_stats: dict = {"avg_corr": None, "max_pair": None, "n_assets": 0}
    var_stats: dict = {"var": None, "cvar": None, "method": None, "sigma": None}
    if returns is not None and not returns.empty:
        corr = correlation_matrix(returns)
        avg = average_pairwise_correlation(corr)
        max_pair = None
        if not corr.empty and corr.shape[0] >= 2:
            vals = corr.to_numpy(dtype=float).copy()
            np.fill_diagonal(vals, np.nan)
            if np.isfinite(vals).any():
                idx = np.nanargmax(vals)
                i, j = np.unravel_index(idx, vals.shape)
                max_pair = {"a": str(corr.index[i]), "b": str(corr.columns[j]),
                            "corr": round(float(vals[i, j]), 4)}
        corr_stats = {"avg_corr": avg, "max_pair": max_pair,
                      "n_assets": int(corr.shape[0]) if not corr.empty else 0}
        cov, aligned_w = cov_from_returns(returns, w, weight_index=frame["code"])
        if cov is not None and aligned_w is not None:
            pv = parametric_var_cvar(aligned_w, cov, confidence=lim.var_confidence)
            var_stats = {**pv, "method": "parametric",
                         "covered_assets": int(aligned_w.size)}
            # 有足够历史时再给一个不依赖正态假设的口径（历史模拟法）
            port_ret = portfolio_return_series(returns, w, weight_index=frame["code"])
            if port_ret is not None:
                var_stats["historical"] = historical_var_cvar(
                    port_ret.to_numpy(dtype=float), confidence=lim.var_confidence)

    dd = drawdown_brake(equity, lim) if equity is not None else {
        "current_drawdown": None, "max_drawdown": None, "brake": 1.0,
        "level": "无净值数据", "triggered": False,
        "n_points": 0, "required": MIN_EQUITY_POINTS, "armed": False}

    breaches: list[str] = []
    actions: list[str] = []
    if conc["top1"] is not None and conc["top1"] > lim.max_single_pct + 1e-9:
        top = frame.sort_values("weight", ascending=False).iloc[0]
        breaches.append(f"单票超限：{top['name']}({top['code']}) {conc['top1']:.1%} "
                        f"> {lim.max_single_pct:.0%}")
        actions.append(f"将 {top['name']} 权重降至 {lim.max_single_pct:.0%} 以下")
    if conc["top3"] is not None and conc["top3"] > lim.max_top3_pct + 1e-9:
        breaches.append(f"前三大持仓合计 {conc['top3']:.1%} > {lim.max_top3_pct:.0%}")
        actions.append(f"分散前三大持仓至合计 {lim.max_top3_pct:.0%} 以下")
    for _, r in ind.iterrows():
        if r["over_limit"]:
            breaches.append(f"行业超限：{r['industry']} {r['weight']:.1%} "
                            f"> {lim.max_industry_pct:.0%}")
            actions.append(f"将 {r['industry']} 行业权重降至 {lim.max_industry_pct:.0%} 以下"
                           f"（当前 {r['weight']:.1%}）")
    avg = corr_stats.get("avg_corr")
    if avg is not None and avg > lim.max_avg_corr + 1e-9:
        breaches.append(f"持仓高度相关：平均两两相关 {avg:.2f} > {lim.max_avg_corr:.2f}")
        actions.append("降低同涨同跌标的的数量，或纳入低相关资产")
    var_v = var_stats.get("var")
    if var_v is not None and var_v > lim.max_var_pct + 1e-9:
        breaches.append(f"单日 VaR({lim.var_confidence:.0%}) {var_v:.2%} "
                        f"> {lim.max_var_pct:.0%}")
        actions.append(f"降低总仓位至使 VaR 落在 {lim.max_var_pct:.0%} 以内")
    if dd.get("triggered"):
        breaches.append(f"回撤熔断：当前回撤 {dd['current_drawdown']:.1%}，"
                        f"仓位系数降至 {dd['brake']:.0%}")
        if dd["brake"] <= 0:
            actions.append("停止开新仓，等待净值修复或明确减仓")
        else:
            actions.append(f"新开仓建议仓位乘以 {dd['brake']:.0%}")
    # 净值曲线偏薄时明说 —— 否则「熔断没触发」与「熔断还没能力判断」看起来一样。
    if equity is not None and not dd.get("armed", False):
        actions.append(equity_curve_status(equity, required=dd.get("required")
                                           or MIN_EQUITY_POINTS)["note"])

    return PortfolioRiskReport(
        n_holdings=int(len(frame)),
        total_weight=total_w,
        concentration=conc,
        by_industry=ind.to_dict("records"),
        correlation=corr_stats,
        var=var_stats,
        drawdown=dd,
        breaches=breaches,
        actions=actions,
        verdict=_verdict(breaches, dd),
        limits=lim.to_dict(),
    )


def scale_new_positions(position_pct: float, brake: float) -> float:
    """把单笔建议仓位按熔断系数缩放（返回比例口径，非百分数）。"""
    b = float(np.clip(brake, 0.0, 1.0))
    return round(max(0.0, float(position_pct) * b), 4)


# 会被熔断缩放的新开仓决策（观察/回避不涉及新仓位，不该被动）
_NEW_POSITION_DECISIONS = ("BUY_NOW", "BUY_ON_PULLBACK")


def apply_brake_to_plans(
    plans: Sequence[dict], brake: float, *, note: bool = True
) -> list[dict]:
    """按熔断系数缩放交易计划里的建议仓位（原地改并返回同一列表）。

    为什么只改 BUY_* ：熔断的目的是「回撤时少开新仓」。观察/回避/卖出本来就不
    涉及新仓位，缩放它们只会让展示数字与实际建议脱节。

    ``position_percent`` 是**百分数**（如 20 表示 20%），与 ``scale_new_positions``
    的比例口径不同，这里单独处理并明确注释，避免两套单位混用。
    """
    b = float(np.clip(brake, 0.0, 1.0))
    if not plans:
        return list(plans or [])
    if b >= 1.0:
        return list(plans)
    for p in plans:
        if not isinstance(p, dict):
            continue
        decision = str(p.get("decision") or "").upper()
        if decision not in _NEW_POSITION_DECISIONS:
            continue
        pp = p.get("position_percent")
        if not isinstance(pp, (int, float)) or not np.isfinite(pp) or pp <= 0:
            continue
        p["position_percent"] = round(float(pp) * b, 2)
        if note:
            tag = f"回撤熔断：仓位系数 {b:.0%}，建议仓位已按比例下调"
            risks = p.get("risks")
            if isinstance(risks, list):
                if tag not in risks:
                    risks.append(tag)
            else:
                p["risks"] = [tag]
    return list(plans)


def summarize(report: PortfolioRiskReport) -> str:
    """渲染成可读文本（报告 / 看板 / 推送复用）。"""
    if report.n_holdings == 0:
        return "组合风控：无持仓"
    lines = [f"组合风控：{report.verdict}",
             f"  持仓 {report.n_holdings} 只，总仓位 {report.total_weight:.1%}，"
             f"有效持仓数 {report.concentration.get('effective_n')}，"
             f"单票最大 {report.concentration.get('top1')}，"
             f"前三大 {report.concentration.get('top3')}"]
    if report.by_industry:
        parts = [f"{r['industry']} {r['weight']:.1%}" for r in report.by_industry[:5]]
        lines.append("  行业暴露：" + "  ".join(parts))
    if report.correlation.get("avg_corr") is not None:
        mp = report.correlation.get("max_pair")
        extra = f"，最高相关 {mp['a']}~{mp['b']} {mp['corr']:.2f}" if mp else ""
        lines.append(f"  平均两两相关 {report.correlation['avg_corr']:.2f}{extra}")
    if report.var.get("var") is not None:
        lines.append(f"  单日 VaR {report.var['var']:.2%} / CVaR "
                     f"{report.var['cvar']:.2%}（参数法，{report.limits['var_confidence']:.0%}）")
    if report.drawdown.get("current_drawdown") is not None:
        lines.append(f"  当前回撤 {report.drawdown['current_drawdown']:.1%}"
                     f"（最大 {report.drawdown['max_drawdown']:.1%}）"
                     f" → 仓位系数 {report.drawdown['brake']:.0%}")
    for b in report.breaches:
        lines.append(f"  ⚠️ {b}")
    for a in report.actions:
        lines.append(f"  → {a}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 净值历史（本模块唯一的持久化部分）
#
# 回撤熔断需要一条「账户净值随时间变化」的序列，而系统里原先没有存过。这里用
# append-only 的 JSON 记录每个交易日的总资产，按日期去重（同日重复运行只保留最后
# 一次），这样即使一天跑多次推送也不会污染净值曲线。
# --------------------------------------------------------------------------- #
def default_equity_history_path() -> "Path":
    import os
    from pathlib import Path

    data_dir = os.environ.get("QTS_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
        # 与 calibration.default_calibrator_path 同口径：config 目录的**同级** results/
        root = base.parent if base.name == "config" else base
        return root / "results" / "equity_history.json"
    return Path(__file__).resolve().parents[1] / "results" / "equity_history.json"


def load_equity_history(path: Optional[Any] = None) -> list[dict]:
    """读取净值历史（``[{date, equity}, ...]``，按日期升序）。文件缺失返回空表。"""
    import json
    from pathlib import Path

    p = Path(path) if path is not None else default_equity_history_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        v = _num(r.get("equity"))
        d = str(r.get("date") or "")
        if v is None or not d:
            continue
        out.append({"date": d, "equity": v})
    out.sort(key=lambda r: r["date"])
    return out


def beijing_date() -> str:
    """北京时间的日历日（``YYYY-MM-DD``）。

    净值历史的日期键必须用**北京时间**：系统其余部分（调度器、实时盯盘、
    持仓缓存）全部按 ``Asia/Shanghai`` 判定交易日，这里若用本机时区，
    在时区不一致的机器上会出现「同一交易日写进两个日期」或「漏一天」，
    回撤熔断的输入顺序随之错乱。
    """
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now().date().isoformat()


def record_equity(
    equity: float,
    *,
    date: Optional[str] = None,
    path: Optional[Any] = None,
) -> list[dict]:
    """记录当日净值（同日覆盖），返回更新后的完整历史。

    ``equity`` 非正或非有限时不写入（净值 0 会让回撤计算除零）。
    未指定 ``date`` 时按**北京时间**取当日，与系统其余交易日口径一致。
    """
    import json
    from pathlib import Path

    v = _num(equity)
    if v is None or v <= 0:
        return load_equity_history(path)
    d = str(date or beijing_date())
    p = Path(path) if path is not None else default_equity_history_path()
    records = [r for r in load_equity_history(p) if r["date"] != d]
    records.append({"date": d, "equity": v})
    records.sort(key=lambda r: r["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def equity_values(records: Sequence[dict]) -> list[float]:
    """从净值记录里取出净值序列（旧→新）。"""
    return [float(r["equity"]) for r in records if _num(r.get("equity"))]
