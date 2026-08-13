"""持仓动作建议：卖出区间 / 回补参考 / 加仓条件（研究辅助，非投资建议）。"""
from __future__ import annotations

from ..utils import get_logger
from .sell_zone import analyze_sell_zone

log = get_logger("HoldingsAction")


def _add_position_hint(row: dict, zone: dict) -> str:
    """粗粒度加仓提示：深套优先反弹减仓；盈利回踩均线可观察；温和亏损看成本区。"""
    pnl = float(zone.get("pnl_pct") or row.get("pnl_pct") or 0)
    regime = zone.get("regime") or ""
    ma20 = zone.get("ma20")
    ma60 = zone.get("ma60")
    price = zone.get("current_price") or row.get("current_price")
    cost = zone.get("cost_price") or row.get("cost_price")

    if regime == "deep_loss" or pnl <= -20:
        return (
            "深套：优先反弹减仓，不宜盲目摊薄；"
            "第一目标附近可分批减，回本前控制仓位。"
        )
    if pnl >= 10:
        return (
            "已有浮盈：回踩 MA20/MA60 且不破止损参考时可观察是否加仓；"
            "追高不加。"
        )
    if pnl >= 0:
        near = []
        if ma20 and price and abs(price - ma20) / max(price, 1e-9) < 0.03:
            near.append("贴近MA20")
        if ma60 and price and abs(price - ma60) / max(price, 1e-9) < 0.03:
            near.append("贴近MA60")
        if near:
            return f"小幅浮盈且{('/'.join(near))}：可规划分批加仓，设好止损。"
        return "小幅浮盈：突破前高放量再考虑加仓，否则持有观察。"
    # mild loss
    if cost and price and price >= cost * 0.95:
        return "接近成本：反弹至成本附近优先减仓或观望，勿急于加仓摊薄。"
    return "浮亏：先看卖出/止损区间；仅当趋势转多且放量突破压力时再评估加仓。"


def analyze_holding_actions(rows: list[dict]) -> list[dict]:
    """对持仓逐只调用 sell_zone，并生成加仓文字提示。"""
    out: list[dict] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        try:
            zone = analyze_sell_zone(row)
            if zone.get("error"):
                out.append({
                    "code": code,
                    "name": row.get("name", ""),
                    "error": zone["error"],
                })
                continue
            item = {
                "code": code,
                "name": zone.get("name") or row.get("name", ""),
                "current_price": zone.get("current_price"),
                "cost_price": zone.get("cost_price"),
                "pnl_pct": zone.get("pnl_pct"),
                "regime": zone.get("regime"),
                "zone_lo": zone.get("zone_lo"),
                "zone_hi": zone.get("zone_hi"),
                "zone_lo_label": zone.get("zone_lo_label"),
                "zone_hi_label": zone.get("zone_hi_label"),
                "stop_loss": zone.get("stop_loss"),
                "advice": zone.get("advice"),
                "stage1_lo": zone.get("stage1_lo"),
                "stage1_hi": zone.get("stage1_hi"),
                "stage2_price": zone.get("stage2_price"),
                "add_hint": _add_position_hint(row, zone),
            }
            out.append(item)
        except Exception as e:  # noqa: BLE001
            log.warning("持仓动作分析失败 %s: %s", code, e)
            out.append({"code": code, "name": row.get("name", ""), "error": str(e)})
    return out


def actions_to_text(actions: list[dict]) -> str:
    lines = ["== 持仓动作建议（卖/买/加仓参考） =="]
    for a in actions:
        if a.get("error"):
            lines.append(f"{a.get('code')} {a.get('name','')} | 分析失败: {a['error']}")
            continue
        lines.append(
            f"{a['code']} {a.get('name','')} | 现价{a.get('current_price')} 成本{a.get('cost_price')} "
            f"盈亏{a.get('pnl_pct')}%"
        )
        lines.append(f"  卖出建议: {a.get('advice')}")
        if a.get("zone_lo") is not None:
            lines.append(
                f"  卖出区间: {a.get('zone_lo')} ~ {a.get('zone_hi')} "
                f"({a.get('zone_lo_label')} → {a.get('zone_hi_label')})"
            )
        if a.get("stop_loss") is not None:
            lines.append(f"  止损参考: {a.get('stop_loss')}")
        if a.get("stage1_lo") is not None:
            lines.append(
                f"  深套分批: 第一目标 {a['stage1_lo']}~{a['stage1_hi']}；"
                f"回本 {a.get('stage2_price')}"
            )
        lines.append(f"  加仓参考: {a.get('add_hint')}")
    return "\n".join(lines)


def actions_to_html(actions: list[dict]) -> str:
    if not actions:
        return "<p>暂无持仓动作分析</p>"
    rows_html = []
    for a in actions:
        if a.get("error"):
            rows_html.append(
                f"<tr><td>{a.get('code')}</td><td colspan='4' style='color:#b91c1c'>"
                f"失败: {a['error']}</td></tr>"
            )
            continue
        sell = a.get("advice") or ""
        zone = ""
        if a.get("zone_lo") is not None:
            zone = f"{a['zone_lo']}~{a['zone_hi']}"
        stop = a.get("stop_loss", "—")
        add = a.get("add_hint") or ""
        rows_html.append(
            f"<tr><td>{a.get('code')}<br/><span style='color:#6b7280'>{a.get('name','')}</span></td>"
            f"<td>{a.get('pnl_pct')}%<br/>现{a.get('current_price')} / 成{a.get('cost_price')}</td>"
            f"<td>{sell}<br/><span style='color:#6b7280'>区间 {zone} 止损 {stop}</span></td>"
            f"<td>{add}</td></tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        "<thead><tr style='background:#f3f4f6'>"
        "<th style='text-align:left;padding:6px'>代码</th>"
        "<th style='text-align:left;padding:6px'>盈亏</th>"
        "<th style='text-align:left;padding:6px'>卖出参考</th>"
        "<th style='text-align:left;padding:6px'>加仓参考</th>"
        "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
        + "<p style='color:#6b7280;font-size:12px'>仅供研究参考，不构成投资建议。</p>"
    )
