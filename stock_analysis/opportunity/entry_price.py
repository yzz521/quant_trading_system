"""入场价格引擎 —— 计算理想/标准/激进三档入场价与入场区间。

不能简单用固定百分比，必须综合：现价、均线、前高前低、支撑/阻力、ATR、
布林、量能、突破位、成交密集区。输出一个「入场区间」而非单一价格。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .risk_reward import MIN_RISK_PCT
from .support_resistance import SupportResistance, detect_support_resistance

#: 入场带相对「标准入场价」的半宽上限（±3%）。
#:
#: 为什么必须有上限：原实现取 ``low = min(ideal, standard)``、``high = aggressive``，
#: 而 ``ideal`` 可以落到 120 日最低点、``aggressive`` 可以落到 120 日最高点 ——
#: 于是「入场区间」实测中位宽 **18.6%**、p90 **72.8%**、最大 **160.5%**。
#: 那不是一个限价买入区间，而是一整段价格走势。
#:
#: 后果（40 只 A 股实测）：
#:   * **57.5%** 的计划出现 ``entry_low < stop_loss`` —— 字面意思是「可以在止损位下方买入」
#:   * **45.0%** 的计划出现 ``entry_high > target_1`` —— 「可以买在目标价上方」
#: 过深的下沿要么永远不成交（说明趋势没回来），要么成交是因为趋势已破（那就不该买）；
#: 过高的上沿已经不是限价单而是市价单。
MAX_ENTRY_HALF_WIDTH = 0.03

#: 半宽下限（±0.5%）。避免区间退化成一条线（``low == high`` 会被判为不可交易）。
MIN_ENTRY_HALF_WIDTH = 0.005

#: 激进入场相对现价的最大溢价（2%）。
#:
#: ``aggressive`` 原可落到 120 日前高（实测某票现价 31.3、上沿 41.49，+32%）。
#: 「突破前高追入」是**条件单**，不是今天能挂的限价买单价，不该撑开入场区间。
#: 超出部分降级为 ``evidence["breakout_hint"]`` 保留提示。
MAX_ENTRY_ABOVE = 0.02


@dataclass
class EntryPrice:
    """入场价格计算结果。"""

    ideal: Optional[float] = None      # 理想入场（回调较深）
    standard: Optional[float] = None   # 标准入场（默认参考）
    aggressive: Optional[float] = None # 激进入场（突破/贴现价）
    low: Optional[float] = None        # 入场区间下沿
    high: Optional[float] = None       # 入场区间上沿
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ideal": self.ideal,
            "standard": self.standard,
            "aggressive": self.aggressive,
            "low": self.low,
            "high": self.high,
            "evidence": self.evidence,
        }


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def calc_entry_zone(
    df: pd.DataFrame,
    sr: Optional[SupportResistance] = None,
    atr_mult_buy: float = 0.5,
) -> EntryPrice:
    """计算入场区间。

    Args:
        df: 已加指标的日K（需 close/ma5/ma20/ma60/atr/boll_*，含 high/low/volume 更好）。
        sr: 预计算的支撑/阻力；None 时内部自动计算。
        atr_mult_buy: 以 ATR 倍数定义标准入场回调深度。
    """
    if df is None or df.empty:
        return EntryPrice()
    d = df.tail(120).reset_index(drop=True)
    cur = _num(d["close"].iloc[-1])
    if cur is None:
        return EntryPrice()

    if sr is None:
        sr = detect_support_resistance(d)

    ma5 = _num(d["ma5"].iloc[-1]) if "ma5" in d.columns else None
    ma20 = _num(d["ma20"].iloc[-1]) if "ma20" in d.columns else None
    ma60 = _num(d["ma60"].iloc[-1]) if "ma60" in d.columns else None
    atr = _num(d["atr"].iloc[-1]) if "atr" in d.columns else None
    boll_low = _num(d["boll_lower"].iloc[-1]) if "boll_lower" in d.columns else None
    boll_mid = _num(d["boll_mid"].iloc[-1]) if "boll_mid" in d.columns else None

    # 前高/前低（排除最后一日，避免用当日高低做突破依据）
    prev_high = _num(d["high"].iloc[:-1].max()) if len(d) > 1 and "high" in d.columns else None
    prev_low = _num(d["low"].iloc[:-1].min()) if len(d) > 1 and "low" in d.columns else None

    evidence: dict = {}

    # 候选基准位：支撑（最近的关键支撑）、MA20、MA60、布林中轨
    bases: list[tuple[float, str]] = []
    if sr and sr.key_support is not None and sr.key_support < cur:
        bases.append((sr.key_support, "关键支撑"))
    if ma20 and ma20 < cur:
        bases.append((ma20, "MA20"))
    if ma60 and ma60 < cur:
        bases.append((ma60, "MA60"))
    if boll_mid and boll_mid < cur:
        bases.append((boll_mid, "BOLL中轨"))
    # ⚠️ ``prev_low`` 必须同样过滤 ``< cur``：它是「近 120 日最低价（不含今日）」，
    # 当今日向下破位时它可以**高于现价**。原实现漏了这一步，于是「标准入场价」
    # 会跑到现价上方（实测 2/40：华工科技现价 86.99、标准入场 90.23），
    # 连带把止损、目标、赔率全部算在了一个买不到的价格上。
    if prev_low and prev_low < cur:
        bases.append((prev_low, "近期低点"))
    if sr and isinstance(sr.evidence, dict):
        fib = sr.evidence.get("fibonacci") or {}
        for key, label in (("fib_618", "斐波那契61.8%"), ("fib_500", "斐波那契50%"), ("fib_382", "斐波那契38.2%")):
            v = _num(fib.get(key))
            if v and v < cur:
                bases.append((v, label))

    if not bases:
        # 无下方参考时，用 ATR 估算回调空间
        atr_v = atr or cur * 0.03
        bases.append((cur - atr_v, "ATR估算"))

    # 标准入场 = 最近的支撑/均线基准（取最接近现价的回调位）
    standard_base, standard_src = max(bases, key=lambda b: b[0])
    standard = standard_base
    evidence["standard"] = {"price": round(standard, 2), "source": standard_src}

    # 理想入场 = 更深一档回调（更下方的基准或再减 0.5*ATR）
    deeper = [b for b in bases if b[0] < standard_base - (atr or cur * 0.02) * 0.3]
    if deeper:
        ideal_base, ideal_src = max(deeper, key=lambda b: b[0])
        ideal = ideal_base
        evidence["ideal"] = {"price": round(ideal, 2), "source": ideal_src}
    else:
        ideal = standard - (atr or cur * 0.03) * 0.5
        evidence["ideal"] = {"price": round(ideal, 2), "source": "标准回调加深0.5ATR"}

    # 激进入场 = 贴现价 / 突破位（现价 0.3%~0.8% 内，或 MA5）
    aggr_candidates = []
    if ma5 and ma5 > cur:
        aggr_candidates.append((ma5, "MA5"))
    if prev_high and prev_high > cur:
        aggr_candidates.append((prev_high, "前高突破"))
    if boll_low and boll_low > cur:
        aggr_candidates.append((boll_low, "BOLL下轨"))
    if aggr_candidates:
        aggressive, aggr_src = min(aggr_candidates, key=lambda b: b[0])
    else:
        aggressive = cur * 1.005
        aggr_src = "贴现价0.5%"
    # 激进入场不应比现价低很多（否则就不是激进了）
    if aggressive < cur * 0.99:
        aggressive = cur * 1.002
        aggr_src = "贴现价0.2%"
    # 上沿不得离现价过远：结构位（前高/布林下轨）属于「突破条件单」，
    # 不是今天能挂的限价买单价 —— 只作提示保留，不撑开入场区间。
    if aggressive > cur * (1 + MAX_ENTRY_ABOVE):
        evidence["breakout_hint"] = {"price": round(aggressive, 2), "source": aggr_src}
        aggressive = cur * (1 + MAX_ENTRY_ABOVE)
        aggr_src = f"限价上沿(现价+{MAX_ENTRY_ABOVE * 100:.0f}%)"
    evidence["aggressive"] = {"price": round(aggressive, 2), "source": aggr_src}

    # ---------- 入场区间 ----------
    # 以「标准入场价」为中心的限价带。
    #
    # 为什么锚在 standard 而不是 min(ideal, standard)：standard 正是下游用来算
    # 止损与目标价的那个价（见 opportunity_engine），区间把它含在内才谈得上自洽；
    # ideal（更深一档回调）只作「深回调提示」，不再撑开下沿。
    half = min(max(aggressive - standard, 0.0), MAX_ENTRY_HALF_WIDTH * standard)
    half = max(half, MIN_ENTRY_HALF_WIDTH * standard)
    low = standard - half
    high = standard + half
    evidence["zone"] = {
        "anchor": "标准入场价",
        "half_width_pct": round(half / standard * 100, 2),
    }
    if ideal < low:
        evidence["deep_pullback_hint"] = {
            "price": round(ideal, 2),
            "source": evidence["ideal"]["source"],
        }
    evidence["low"] = round(low, 2)
    evidence["high"] = round(high, 2)

    return EntryPrice(
        ideal=round(ideal, 2),
        standard=round(standard, 2),
        aggressive=round(aggressive, 2),
        low=round(low, 2),
        high=round(high, 2),
        evidence=evidence,
    )


def reconcile_entry_zone(
    low: Optional[float],
    high: Optional[float],
    *,
    stop_loss: Optional[float],
    target_1: Optional[float],
    min_risk_pct: float = MIN_RISK_PCT,
) -> tuple[Optional[float], Optional[float], bool, str]:
    """把入场区间夹到与止损/目标几何自洽的范围内。

    规则：
      * 下沿必须高于止损 —— 否则计划在教用户「在止损位下方买入」
      * 上沿必须低于目标 —— 否则计划在教用户「买在目标价上方」
      * 夹逼后 ``low >= high`` → 区间为空 → 计划不可交易

    Args:
        low/high: ``calc_entry_zone`` 输出的区间。
        stop_loss/target_1: 同一计划的止损与一档目标。
        min_risk_pct: 夹逼时留出的缓冲（与 ``risk_reward.MIN_RISK_PCT`` 同源）。

    Returns:
        ``(low, high, ok, note)``。``ok=False`` 时原样返回入参，由调用方作废该计划。
    """
    if low is None or high is None:
        return low, high, True, ""
    if not stop_loss or not target_1:
        return low, high, True, ""
    if stop_loss >= target_1:
        return low, high, False, (
            f"止损 {stop_loss} 不低于目标 {target_1}，计划无效"
        )
    new_low = max(low, stop_loss * (1 + min_risk_pct))
    new_high = min(high, target_1 * (1 - min_risk_pct))
    if new_low >= new_high:
        return low, high, False, (
            f"入场区间 {low}~{high} 与止损 {stop_loss}/目标 {target_1} 无法自洽"
            f"（止损到目标之间没有可下单的空间）"
        )
    note = ""
    if new_low > low or new_high < high:
        note = (
            f"入场区间已按止损/目标夹逼：{low}~{high} → "
            f"{round(new_low, 2)}~{round(new_high, 2)}"
        )
    return round(new_low, 2), round(new_high, 2), True, note
