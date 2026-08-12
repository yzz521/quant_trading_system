"""Stock Score —— 个股质量评分（0-100）。

回答「这只股票本身好不好」。
权重（计划书 §05）：
  基本面质量 20% | 技术趋势 25% | 资金流 15% | 估值 10% | 市场环境 10% | 风险 20%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .score_components import normalize_component, score_trend

WEIGHTS = {
    "fundamental": 0.20,
    "technical": 0.25,
    "capital_flow": 0.15,
    "valuation": 0.10,
    "market_env": 0.10,
    "risk": 0.20,
}


@dataclass
class StockScore:
    """个股质量评分结果。"""

    total: float = 0.0
    components: dict = field(default_factory=dict)  # 各维度 0-100
    breakdown: dict = field(default_factory=dict)   # 权重明细

    def to_dict(self) -> dict:
        return {"total": round(self.total, 1), "components": self.components, "breakdown": self.breakdown}


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _score_fundamental(df: pd.DataFrame, extra: Optional[dict] = None) -> float:
    """基本面质量：优先用外部传入（财务数据/市值/行业），缺省用市值与换手兜底。"""
    extra = extra or {}
    s = 50.0
    n = 0
    if "total_cap_yi" in extra and extra["total_cap_yi"] is not None:
        cap = _num(extra["total_cap_yi"])
        if cap:
            # 中小市值（50~500 亿）在 A 股弹性较好，1000 亿以上偏稳
            s += normalize_component(cap, 10, 300, invert=True) * 0.4 - 10
            n += 1
    if "turnover" in extra and extra["turnover"] is not None:
        to = _num(extra["turnover"])
        if to is not None:
            s += (normalize_component(to, 0.3, 8.0) - 50) * 0.3
            n += 1
    if "roe" in extra and extra["roe"] is not None:
        roe = _num(extra["roe"])
        if roe is not None:
            s += (normalize_component(roe, 0, 20) - 50) * 0.5
            n += 1
    if n == 0:
        # 无财务数据：用日均成交额（流动性 = 可交易性）代替
        if df is not None and "amount" in df.columns and len(df) > 0:
            amt = pd.to_numeric(df["amount"], errors="coerce").tail(20).mean()
            if amt is not None and not np.isnan(amt):
                s = normalize_component(float(amt), 5e7, 2e9)
    return float(np.clip(s, 0, 100))


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


def _score_risk(df: pd.DataFrame, news_risks: Optional[list] = None) -> float:
    """风险维度：技术风险（高位/超买）+ 新闻风险关键词。分数越高 = 风险越低（越安全）。"""
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
        # 超买（RSI6 > 80）
        if "rsi6" in d.columns:
            rsi = pd.to_numeric(d["rsi6"], errors="coerce").iloc[-1]
            if rsi is not None and not np.isnan(rsi) and rsi > 80:
                s -= 10
    news_risks = news_risks or []
    if news_risks:
        s -= min(30, len(news_risks) * 8)
    return float(np.clip(s, 0, 100))


def calc_stock_score(
    df: Optional[pd.DataFrame] = None,
    *,
    extra: Optional[dict] = None,
    regime_score: Optional[float] = None,
    news_risks: Optional[list] = None,
    weights: Optional[dict] = None,
) -> StockScore:
    """计算个股质量评分。

    Args:
        df: 已加指标的日K（technical/risk 维度使用；可为 None，此时用 extra 兜底）。
        extra: 外部数据（市值/PE/换手/主力净流入/金额/ROE 等）。
        regime_score: 市场环境分（0-100）。
        news_risks: 新闻风险列表（命中关键词的条目）。
        weights: 自定义权重（默认按计划书 §05）。
    """
    w = {**WEIGHTS, **(weights or {})}
    extra = extra or {}

    comps = {
        "fundamental": _score_fundamental(df, extra),
        "technical": score_trend(df) if df is not None else 50.0,
        "capital_flow": _score_capital_flow(df, extra),
        "valuation": _score_valuation(df, extra),
        "market_env": _score_market_env(regime_score),
        "risk": _score_risk(df, news_risks),
    }
    total = sum(comps[k] * w[k] for k in w)
    breakdown = {k: {"weight": round(w[k], 3), "score": round(comps[k], 1)} for k in w}
    return StockScore(total=total, components={k: round(v, 1) for k, v in comps.items()}, breakdown=breakdown)
