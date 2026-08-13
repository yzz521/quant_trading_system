"""持仓卖出区间分析 — 回答「我持有的 X，什么价位区间合适卖掉？」.

对每只持仓，取最近约一年的日 K 线，综合四类信息给出结论:

* 成本价锚点: 浮动盈亏 + 成本价向上 +10% / +20% / +30% 的止盈价位
* 技术压力位: 近 20 / 60 / 120 日高点、布林带上轨、高于现价的均线
* 建议卖出区间: 当前价上方最近的 1~2 个目标位（成本锚点与技术压力合并、去重、排序）
* 止损参考: 盈利持仓取 MA20 与成本价较高者；亏损持仓取 MA20 与再跌 5% 较低者

用法::

    from quant_trading_system.stock_analysis.sell_zone import analyze_sell_zone
    r = analyze_sell_zone({"code": "513310", "cost_price": 4.7254})
    print(r["advice"])

单只标的取数失败时返回带 ``error`` 字段的字典，不影响批量分析其它标的。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .data_fetcher import detect_market, fetch_kline
from .indicators import add_all_indicators


def _f(v) -> Optional[float]:
    """Convert to float if possible; NaN / error → None."""
    try:
        f = float(v)
        return f if not pd.isna(f) else None
    except Exception:  # noqa: BLE001
        return None


def analyze_sell_zone(position: dict, days: int = 250,
                      profit_steps: tuple = (0.10, 0.20, 0.30)) -> dict:
    """Analyze one position and return its sell-zone recommendation.

    ``position`` needs at least ``code`` and ``cost_price``; optional
    ``name`` / ``market`` / ``quantity`` / ``buy_date`` are carried into
    the result. Never raises — failures are returned as ``error`` entries.
    """
    code = str(position.get("code", "")).strip().upper()
    cost = float(position.get("cost_price", 0) or 0)
    out = {
        "code": code,
        "name": position.get("name") or code,
        "market": position.get("market", "CN"),
        "cost_price": round(cost, 4),
        "quantity": position.get("quantity", 0),
        "buy_date": position.get("buy_date", ""),
    }
    if not code:
        out["error"] = "缺少股票代码"
        return out

    try:
        info = detect_market(code)
        df = fetch_kline(info, days=days)
        if df is None or df.empty or len(df) < 30:
            raise ValueError(f"{code} 行情数据不足"
                             f"（{0 if df is None else len(df)} 行）")
        df = add_all_indicators(df)
        last = df.iloc[-1]
        current = float(last["close"])

        pnl_pct = (current - cost) / cost * 100 if cost else 0.0

        ma5, ma10, ma20, ma60 = (_f(last[x]) for x in ("ma5", "ma10", "ma20", "ma60"))
        boll_upper = _f(last["boll_upper"])
        boll_mid = _f(last["boll_mid"])
        boll_lower = _f(last["boll_lower"])
        high20 = _f(df["high"].tail(20).max())
        high60 = _f(df["high"].tail(60).max())
        high120 = _f(df["high"].tail(min(120, len(df))).max()) if len(df) >= 120 else None
        atr = _f(last["atr"])

        # ---- 候选目标位：成本锚点 + 技术压力位，去重并按价格排序 ----
        levels: dict[str, float] = {}
        for s in profit_steps:
            levels[f"成本+{int(s * 100)}%"] = cost * (1 + s)
        if cost > current:
            # 亏损持仓：先把「回本价」作为首要解套目标
            levels["回本价(成本)"] = cost
        for label, v in (("20日高点", high20), ("60日高点", high60),
                         ("120日高点", high120), ("布林上轨", boll_upper)):
            if v is not None:
                levels[label] = v

        seen: dict[float, list[str]] = {}
        for label, v in levels.items():
            # 只保留当前价上方至少 0.5% 的候选（避免前高恰好等于现价）
            if v is not None and v > current * 1.005:
                seen.setdefault(round(v, 4), []).append(label)
        targets = []
        for p, labels in sorted(seen.items()):
            pct = round((p - current) / current * 100, 1)
            # near: 到成本前的压力；exit: 回本；far: 成本上方止盈
            if cost > current and p < cost * 0.998:
                tier = "near"
            elif cost > 0 and abs(p - cost) / cost <= 0.005:
                tier = "exit"
            elif cost > current and p > cost:
                tier = "far"
            else:
                tier = "near" if pct <= 15 else "far"
            targets.append({
                "price": p, "labels": labels, "pct": pct, "tier": tier,
            })

        # ---- 建议卖出区间：现价上方最近的 1~2 个目标位 ----
        # 深度亏损（<= -20%）：优先「反弹减仓 → 回本」，技术位仅作途中参考
        deep_loss = pnl_pct <= -20.0
        stage1 = None  # (lo, hi, lo_label, hi_label) 近端分批
        stage2 = None  # 最终回本

        if deep_loss and cost > current:
            # 途中反弹位：现价与成本之间的技术压力
            mid = round((current + cost) / 2.0, 4)
            path_levels = []
            for label, v in (("布林中轨", boll_mid), ("MA20", ma20),
                             ("MA60", ma60), ("20日高点", high20),
                             ("60日高点", high60)):
                if v is not None and current * 1.005 < v < cost * 0.995:
                    path_levels.append((round(v, 4), label))
            path_levels.sort(key=lambda x: x[0])
            if path_levels:
                lo_p, lo_l = path_levels[0]
                if len(path_levels) >= 2:
                    hi_p, hi_l = path_levels[1]
                else:
                    hi_p, hi_l = lo_p, lo_l
                stage1 = (lo_p, hi_p, lo_l, hi_l)
                stage2 = (round(cost, 4), round(cost, 4), "回本价(成本)", "回本价(成本)")
                # 汇总表仍用「第一目标下沿 → 回本」大区间，便于一眼看到路径
                zone = (lo_p, round(cost, 4), lo_l, "回本价(成本)")
            else:
                stage1 = (mid, mid, "反弹中位", "反弹中位")
                stage2 = (round(cost, 4), round(cost, 4), "回本价(成本)", "回本价(成本)")
                zone = (mid, round(cost, 4), "反弹中位", "回本价(成本)")
        elif not targets:
            bump1 = max((atr or 0.0), current * 0.02)
            bump2 = max(2 * (atr or 0.0), current * 0.05)
            zone = (round(current + bump1, 4), round(current + bump2, 4),
                    "现价+1ATR", "现价+2ATR")
        else:
            first, *rest = targets
            if rest:
                zone = (first["price"], rest[0]["price"],
                        first["labels"][0], rest[0]["labels"][0])
            else:
                zone = (first["price"], round(first["price"] * 1.03, 4),
                        first["labels"][0], "目标+3%缓冲")

        # ---- 止损参考 ----
        if pnl_pct >= 0:
            cand = [x for x in (ma20, cost * 0.98) if x is not None]
            stop = round(max(cand), 4) if cand else None
            stop_note = "盈利持仓：跌破 MA20 或成本价，保护利润"
        elif deep_loss:
            # 深套：止损偏「再亏幅度」，避免被 MA20 过近扫出；默认再跌 8% 或布林下轨
            cand = [x for x in (boll_lower, current * 0.92) if x is not None]
            stop = round(min(cand), 4) if cand else round(current * 0.92, 4)
            stop_note = "深套持仓：再跌约 8% 或跌破布林下轨，控制继续扩大亏损"
        else:
            cand = [x for x in (ma20, current * 0.95) if x is not None]
            stop = round(min(cand), 4) if cand else None
            stop_note = "亏损持仓：跌破 MA20 或再跌 5%，控制回撤"

        # ---- 一句话建议 ----
        parts = []
        if pnl_pct >= 0:
            parts.append(f"当前盈利 {pnl_pct:+.1f}%")
        elif deep_loss:
            parts.append(
                f"当前深套 {pnl_pct:+.1f}%（距回本 {-pnl_pct:.1f}%）；"
                f"优先反弹减仓，不宜仅按短期压力位清仓"
            )
            if stage1 is not None:
                parts.append(
                    f"第一目标（分批减仓）{stage1[0]} ~ {stage1[1]}"
                    f"（{stage1[2]} → {stage1[3]}）"
                )
            if stage2 is not None:
                parts.append(f"最终目标回本 {stage2[0]}（{stage2[2]}）")
        else:
            parts.append(f"当前亏损 {pnl_pct:+.1f}%（距回本 {-pnl_pct:.1f}%）")
        if not deep_loss:
            parts.append(f"建议卖出区间 {zone[0]} ~ {zone[1]}"
                         f"（{zone[2]} → {zone[3]}）")
        elif stage1 is None:
            parts.append(f"建议卖出区间 {zone[0]} ~ {zone[1]}"
                         f"（{zone[2]} → {zone[3]}）")
        if stop is not None:
            parts.append(f"止损参考 {stop}（{stop_note}）")
        advice = "；".join(parts) + "。"

        regime = "profit" if pnl_pct >= 0 else ("deep_loss" if deep_loss else "mild_loss")
        out.update({
            "current_price": round(current, 4),
            "pnl_pct": round(pnl_pct, 2),
            "regime": regime,
            "stage1_lo": stage1[0] if stage1 else None,
            "stage1_hi": stage1[1] if stage1 else None,
            "stage1_lo_label": stage1[2] if stage1 else None,
            "stage1_hi_label": stage1[3] if stage1 else None,
            "stage2_price": stage2[0] if stage2 else None,
            "stage2_label": stage2[2] if stage2 else None,
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "boll_upper": boll_upper, "boll_mid": boll_mid, "boll_lower": boll_lower,
            "high20": high20, "high60": high60, "high120": high120,
            "atr": atr,
            "targets": targets,
            "zone_lo": zone[0], "zone_hi": zone[1],
            "zone_lo_label": zone[2], "zone_hi_label": zone[3],
            "stop_loss": stop, "stop_note": stop_note,
            "advice": advice,
        })
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{e}"
    return out


def analyze_positions(positions: list[dict], **kwargs) -> list[dict]:
    """Analyze many positions; a single failure degrades to an error entry."""
    return [analyze_sell_zone(p, **kwargs) for p in positions]
