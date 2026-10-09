"""Stock Score —— 个股质量评分（0-100）。

回答「这只股票本身好不好」。
权重（Factor Engine，9 因子，和=1.00）：
  基本面质量 12% | 成长 8% | 技术趋势 20% | 动量 5% | 资金流 15%
  | 估值 10% | 市场环境 5% | 板块强度 5% | 风险 20%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .score_components import normalize_component, score_momentum, score_trend

WEIGHTS = {
    "fundamental": 0.12,
    "growth": 0.08,
    "technical": 0.20,
    "momentum": 0.05,
    "capital_flow": 0.15,
    "valuation": 0.10,
    "market_env": 0.05,
    "sector": 0.05,
    "risk": 0.20,
}


@dataclass
class StockScore:
    """个股质量评分结果。"""

    total: float = 0.0
    components: dict = field(default_factory=dict)  # 各维度 0-100
    breakdown: dict = field(default_factory=dict)   # 权重明细
    gated_dims: list = field(default_factory=list)  # 因缺数据被剔除加权的维度

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "components": self.components,
            "breakdown": self.breakdown,
            "gated_dims": list(self.gated_dims),
        }


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _score_fundamental(df: pd.DataFrame, extra: Optional[dict] = None) -> float:
    """基本面质量：**只由财务数据（ROE）驱动**。

    为什么要改（2026-10 裁决）
    --------------------------
    原实现把**行情快照**（总市值 / 换手率）当成基本面代理：

        s += normalize_component(cap, 10, 300, invert=True) * 0.4 - 10

    市值 > 300 亿时 invert 分 ≈ 0 ⇒ **硬扣 10 分**；低换手（蓝筹常态 < 2%）再扣约 10 分。
    而唯一能拉高该维度分的 ``roe`` 数据源又不提供 ⇒ A 股蓝筹的基本面分只剩约 30，
    远低于 50 中性。实测 40 只跨行业 A 股**没有一只** stock_score ≥ 60，
    33 只候选全部卡在「质量分 < 60」门槛下 1~2 分。

    语义上这也是错的：把「小盘弹性」这种**成长/机会**诉求塞进「基本面**质量**」维度，
    结果是大盘蓝筹在「质量」上被判劣于小盘股 —— 与常识相反。

    现在：有 ROE → 按 ROE 打分；没有 ROE → 返回 50 中性，并由 ``calc_stock_score``
    把该维度从加权中**剔除**（缺失 ≠ 中性，不该用「没数据」稀释其他有数据的维度）。
    """
    extra = extra or {}
    roe = _num(extra.get("roe"))
    if roe is not None:
        return float(np.clip(50.0 + (normalize_component(roe, 0, 20) - 50) * 0.5, 0, 100))
    # 无财务数据：用日均成交额（流动性 = 可交易性）作弱兜底，仅供展示 ——
    # 该维度会被 calc_stock_score 剔除加权，所以兜底值不参与总分。
    if df is not None and "amount" in df.columns and len(df) > 0:
        amt = pd.to_numeric(df["amount"], errors="coerce").tail(20).mean()
        if amt is not None and not np.isnan(amt):
            return float(np.clip(normalize_component(float(amt), 5e7, 2e9), 0, 100))
    return 50.0


def _score_capital_flow(df: Optional[pd.DataFrame], extra: Optional[dict] = None) -> float:
    """资金流：主力净流入占比 + OBV 形态。"""
    extra = extra or {}
    s = 50.0
    if extra.get("main_net") is not None:
        main_net = _num(extra["main_net"])
        if main_net is not None:
            amount = _num(extra.get("amount") or (df["amount"].iloc[-1] if df is not None and "amount" in df.columns else None))
            if amount:
                ratio = main_net / amount
                s += (normalize_component(ratio, -0.1, 0.15) - 50) * 0.8
    if df is not None and "obv" in df.columns and len(df) > 20:
        obv = pd.to_numeric(df["obv"], errors="coerce").tail(20)
        if obv.isna().all() is False and obv.iloc[-1] != 0:
            slope = (obv.iloc[-1] - obv.iloc[0]) / abs(obv.iloc[0]) if obv.iloc[0] != 0 else 0
            s += float(np.clip(slope * 200, -15, 15))
    return float(np.clip(s, 0, 100))


def _score_valuation(df: pd.DataFrame, extra: Optional[dict] = None) -> float:
    """估值：PE 落在合理区间（10~40）最优。"""
    extra = extra or {}
    pe = _num(extra.get("pe"))
    if pe is None:
        return 50.0
    if pe <= 0:  # 亏损股估值天然差
        return 30.0
    if 10 <= pe <= 40:
        return 85.0
    if 40 < pe <= 80:
        return 60.0
    if pe < 10:
        return 70.0  # 低估值（需结合基本面看，这里给中性偏上）
    return 35.0


def _score_market_env(regime_score: Optional[float]) -> float:
    """市场环境：由外部市场状态打分器给出 0-100。"""
    return float(np.clip(regime_score if regime_score is not None else 50, 0, 100))


def _score_growth(extra: Optional[dict] = None) -> float:
    """成长（Growth）：营收/净利同比增速。缺数据 → 50 中性。"""
    extra = extra or {}
    rev = _num(extra.get("rev_yoy"))
    profit = _num(extra.get("profit_yoy"))
    if rev is None and profit is None:
        return 50.0
    s = 50.0
    n = 0
    if profit is not None:  # 净利同比 -20%~50% 线性（权重 0.6）
        s += (normalize_component(profit, -20, 50) - 50) * 0.6
        n += 1
    if rev is not None:  # 营收同比 -10%~40%（权重 0.4）
        s += (normalize_component(rev, -10, 40) - 50) * 0.4
        n += 1
    return float(np.clip(s, 0, 100))


def _score_sector(sector_score: Optional[float]) -> float:
    """板块强度（Sector Rotation）：由外部板块轮动模块给出 0-100，未命中 50。"""
    return float(np.clip(sector_score if sector_score is not None else 50, 0, 100))


def _unique_news_keys(items: Optional[list]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        if isinstance(it, dict):
            k = str(it.get("keyword") or it.get("title") or "").strip()
        else:
            k = str(it).strip()
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def _score_risk(
    df: pd.DataFrame,
    news_risks: Optional[list] = None,
    news_catalysts: Optional[list] = None,
) -> float:
    """风险维度：技术风险（高位/超买）+ 信息面（公告/新闻，一类关键词一票）。

    分数越高 = 越安全。无新闻时不改分（与未接入信息面时行为一致）。
    """
    s = 80.0
    if df is not None and len(df) > 0:
        d = df.tail(20).reset_index(drop=True)
        close = pd.to_numeric(d["close"], errors="coerce")
        # 距 60 日高点太近 → 回撤风险
        if len(d) >= 20:
            hi = float(close.max())
            cur = float(close.iloc[-1])
            if hi > 0:
                from_high = (hi - cur) / hi
                if from_high > 0.15:
                    s -= 15
        # 超买：RSI / KDJ / CCI 只作风险过滤，不另开评分因子（彼此高度相关）
        if "rsi6" in d.columns:
            rsi = pd.to_numeric(d["rsi6"], errors="coerce").iloc[-1]
            if rsi is not None and not np.isnan(rsi) and rsi > 80:
                s -= 10
        if "j" in d.columns:
            j = pd.to_numeric(d["j"], errors="coerce").iloc[-1]
            if j is not None and not np.isnan(j) and j > 100:
                s -= 6
        if "cci" in d.columns:
            cci = pd.to_numeric(d["cci"], errors="coerce").iloc[-1]
            if cci is not None and not np.isnan(cci) and cci > 200:
                s -= 6
        if "wr" in d.columns:
            wr = pd.to_numeric(d["wr"], errors="coerce").iloc[-1]
            if wr is not None and not np.isnan(wr) and wr > -20:
                s -= 4
    risk_keys = _unique_news_keys(news_risks)
    if risk_keys:
        s -= min(30, len(risk_keys) * 8)
        if any(
            (it.get("severity") == "severe") if isinstance(it, dict) else False
            for it in (news_risks or [])
        ):
            s -= 8
    cat_keys = _unique_news_keys(news_catalysts)
    if cat_keys:
        s += min(15, len(cat_keys) * 5)
    return float(np.clip(s, 0, 100))


#: 缺数据时会返回「中性 50」的维度。若把 50 按原权重计入，等于用「没数据」
#: 稀释其他有数据的维度 —— 实测 ``capital_flow`` 权重 15%，但 ``main_net``
#: 数据源不提供 ⇒ 它恒为 50，对排序毫无贡献却占着 15% 的权重，**完全失效**。
#: 这些维度在数据缺失时从加权中剔除，权重按比例分给其余维度。
_DATA_GATED_DIMS = ("fundamental", "growth", "capital_flow", "valuation",
                    "market_env", "sector")

#: 重归一化后至少要保留的维度数。低于它说明数据整体太差，重归一化会让少数
#: 维度权重畸高（把全部排序押在一两个维度上比原加权更危险）→ 回退原加权。
_MIN_ACTIVE_DIMS = 5


def _dim_has_data(dim: str, extra: dict, regime_score, sector_score) -> bool:
    """该维度的输入数据是否**真的**存在（而不是「用了默认中性值」）。"""
    if dim == "fundamental":
        return _num(extra.get("roe")) is not None
    if dim == "growth":
        return (_num(extra.get("rev_yoy")) is not None
                or _num(extra.get("profit_yoy")) is not None)
    if dim == "capital_flow":
        return _num(extra.get("main_net")) is not None
    if dim == "valuation":
        return _num(extra.get("pe")) is not None
    if dim == "market_env":
        return regime_score is not None
    if dim == "sector":
        return sector_score is not None
    return True


def calc_stock_score(
    df: Optional[pd.DataFrame] = None,
    *,
    extra: Optional[dict] = None,
    regime_score: Optional[float] = None,
    sector_score: Optional[float] = None,
    news_risks: Optional[list] = None,
    news_catalysts: Optional[list] = None,
    weights: Optional[dict] = None,
) -> StockScore:
    """计算个股质量评分（9 因子）。

    Args:
        df: 已加指标的日K（technical/momentum/risk 维度使用；可为 None，此时用 extra 兜底）。
        extra: 外部数据（市值/PE/换手/主力净流入/营收净利同比/金额/ROE 等）。
        regime_score: 市场环境分（0-100）。
        sector_score: 板块强度分（0-100，Sector Rotation 环节）。
        news_risks: 新闻/公告风险条目（按关键词去重后计入 risk，不另开因子）。
        news_catalysts: 利好催化剂条目（同一 risk 票里小幅加分，与风险对冲）。
        weights: 自定义权重（默认 9 因子权重）。
    """
    w = {**WEIGHTS, **(weights or {})}
    extra = extra or {}

    comps = {
        "fundamental": _score_fundamental(df, extra),
        "growth": _score_growth(extra),
        "technical": score_trend(df) if df is not None else 50.0,
        "momentum": score_momentum(df) if df is not None else 50.0,
        "capital_flow": _score_capital_flow(df, extra),
        "valuation": _score_valuation(df, extra),
        "market_env": _score_market_env(regime_score),
        "sector": _score_sector(sector_score),
        "risk": _score_risk(df, news_risks, news_catalysts),
    }

    # 缺失 ≠ 中性：把「没有数据、只能给 50」的维度从加权里剔除，
    # 权重按比例分给有数据的维度。否则 50 分会稀释真实信号（见 _DATA_GATED_DIMS）。
    gated = [k for k in w if k in _DATA_GATED_DIMS
             and not _dim_has_data(k, extra, regime_score, sector_score)]
    active = {k: v for k, v in w.items() if k not in gated}
    if gated and len(active) >= _MIN_ACTIVE_DIMS and sum(active.values()) > 0:
        scale = sum(w.values()) / sum(active.values())
        w_eff = {k: (v * scale if k in active else 0.0) for k, v in w.items()}
    else:
        w_eff = dict(w)

    total = sum(comps[k] * w_eff[k] for k in w_eff)
    breakdown = {k: {"weight": round(w_eff[k], 3), "score": round(comps[k], 1)} for k in w_eff}
    return StockScore(
        total=total,
        components={k: round(v, 1) for k, v in comps.items()},
        breakdown=breakdown,
        gated_dims=gated,
    )
