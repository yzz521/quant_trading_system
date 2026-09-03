"""持仓量化：把机会引擎按「已持有」解读，不当时机票。

动作只有四档：SELL / REDUCE / HOLD / ADD。
不跑历史回测；实时路径可拉信息面，回测/单测保持 fetch_news=False。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..utils import get_logger
from .data_fetcher import detect_market
from .opportunity.batch_scanner import _default_loader
from .opportunity.opportunity_engine import OpportunityEngine
from .opportunity.trading_plan import TradingPlan

log = get_logger("HoldingsQuant")

ACTION_LABEL = {"SELL": "卖出", "REDUCE": "减仓", "HOLD": "持有", "ADD": "可加仓"}
ACTION_EMOJI = {"SELL": "🔴", "REDUCE": "🟠", "HOLD": "🟡", "ADD": "🟢"}

_CACHE_DEFAULT = Path(__file__).resolve().parents[1] / "results" / "holdings_quant.json"


def session_date() -> str:
    """交易日缓存键：北京时间的日历日。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now().date().isoformat()


def cache_path(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root) / "results" / "holdings_quant.json"
    env = os.environ.get("QTS_DATA_DIR")
    if env:
        p = Path(env)
        base = p.parent if p.name == "config" else p
        return base / "results" / "holdings_quant.json"
    return _CACHE_DEFAULT


def load_cache(path: Optional[Path] = None) -> dict:
    p = path or cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_market_cache(market: str, date_str: str, items: list[dict], path: Optional[Path] = None) -> None:
    p = path or cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_cache(p)
    data[market] = {
        "date": date_str,
        "at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cached_items(market: str, date_str: str, path: Optional[Path] = None) -> Optional[list[dict]]:
    block = load_cache(path).get(market) or {}
    if block.get("date") == date_str and isinstance(block.get("items"), list):
        return block["items"]
    return None


def _meta(plan) -> dict:
    if plan is None:
        return {}
    if isinstance(plan, dict):
        return plan.get("meta") or {}
    return getattr(plan, "meta", None) or {}


def _num(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None  # NaN
    except (TypeError, ValueError):
        return None


def interpret_holding_action(
    plan: Optional[TradingPlan] = None,
    position: Optional[dict] = None,
    zone: Optional[dict] = None,
) -> dict:
    """Map a new-entry TradingPlan onto an existing position.

    Stop / 重大风险 / 趋势转空优先于「RR 不够所以 AVOID」。
    """
    position = position or {}
    zone = zone or {}
    price = _num(
        (getattr(plan, "current_price", None) if plan is not None and not isinstance(plan, dict)
         else (plan or {}).get("current_price") if isinstance(plan, dict) else None)
        or position.get("current_price")
        or zone.get("current_price")
    )
    if isinstance(plan, dict):
        stop = _num(zone.get("stop_loss") or plan.get("stop_loss"))
        t1 = _num(plan.get("target_1"))
        entry_low = _num(plan.get("entry_low"))
        entry_high = _num(plan.get("entry_high"))
        orig = plan.get("decision") or ""
    else:
        stop = _num(zone.get("stop_loss") or (getattr(plan, "stop_loss", None) if plan else None))
        t1 = _num(getattr(plan, "target_1", None) if plan else None)
        entry_low = _num(getattr(plan, "entry_low", None) if plan else None)
        entry_high = _num(getattr(plan, "entry_high", None) if plan else None)
        orig = ""
        d = getattr(plan, "decision", None)
        if d is not None:
            orig = d.value if hasattr(d, "value") else str(d)
    orig = str(orig or "").split(".")[-1]

    pnl = _num(zone.get("pnl_pct") or position.get("pnl_pct")) or 0.0
    meta = _meta(plan)
    tech = meta.get("technical") or {}
    info = meta.get("information") or {}
    tags = list(tech.get("tags") or [])
    tech_grade = tech.get("grade") or ""
    info_grade = info.get("grade") or ""
    severe = bool(info.get("severe"))
    bearish = any(t in tags for t in ("均线空头", "MACD空头", "ADX空头趋势"))

    action = "HOLD"
    note = "持有观察"

    if stop is not None and price is not None and price <= stop * 1.002:
        action, note = "SELL", "现价触及止损参考"
    elif severe:
        action, note = "REDUCE", "信息面重大风险，优先减仓"
    elif tech_grade == "C" and bearish:
        action, note = "REDUCE", "技术面转空，减仓观察"
    elif (zone.get("regime") == "deep_loss" or pnl <= -20) and action == "HOLD":
        note = "深套：反弹减仓，勿摊薄"
    elif t1 is not None and price is not None and pnl >= 8 and price >= t1 * 0.98:
        action, note = "REDUCE", "接近第一目标，可兑现部分"
    elif (
        pnl >= 0
        and tech_grade in ("S", "A")
        and not severe
        and not bearish
        and price is not None
        and entry_low is not None
        and entry_high is not None
        and entry_low <= price <= entry_high * 1.01
    ):
        action, note = "ADD", "趋势仍在且回踩入场区，可观察加仓"
    elif orig == "AVOID":
        note = "按新票标准 RR 偏弱：持有、不加仓"

    return {
        "action": action,
        "action_label": ACTION_LABEL[action],
        "action_emoji": ACTION_EMOJI[action],
        "note": note,
        "tech_grade": tech_grade or "—",
        "info_grade": info_grade or "—",
        "pnl_pct": round(pnl, 2),
        "stop_loss": round(stop, 2) if stop is not None else None,
        "current_price": round(price, 2) if price is not None else None,
    }


def _item_from_plan(row: dict, res, zone: Optional[dict]) -> dict:
    plan = res.plan if res is not None else None
    mapped = interpret_holding_action(plan, row, zone)
    code = str(row.get("code") or "")
    name = (plan.name if plan else None) or row.get("name") or code
    stock_score = plan.stock_score if plan else None
    opp_score = plan.opportunity_score if plan else None
    reasons = list(plan.reasons or []) if plan else []
    risks = list(plan.risks or []) if plan else []
    info = _meta(plan).get("information") or {}
    tech = _meta(plan).get("technical") or {}
    return {
        "code": code,
        "name": name,
        "market": row.get("market") or detect_market(code).market,
        "quantity": row.get("quantity"),
        "cost_price": row.get("cost_price"),
        "stock_score": stock_score,
        "opportunity_score": opp_score,
        "reasons": reasons[:3],
        "risks": risks[:2],
        "headlines": (info.get("headlines") or [])[:2],
        "technical": tech,
        "information": info,
        **mapped,
    }


def analyze_holdings_quant(
    rows: list[dict],
    *,
    engine: Optional[OpportunityEngine] = None,
    zones: Optional[dict] = None,
    fetch_news: bool = True,
    regime_score: Optional[float] = None,
    sector_map: Optional[dict] = None,
    sector_rank: Optional[list] = None,
) -> list[dict]:
    """逐只持仓跑机会引擎并映射为持有动作。单票失败不影响其余。"""
    eng = engine or OpportunityEngine(
        fetch_news=fetch_news,
        regime_score=regime_score,
        sector_map=sector_map or {},
        sector_rank=sector_rank or [],
        account_equity=None,
    )
    zones = zones or {}
    out: list[dict] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        name = str(row.get("name") or code)
        try:
            market = str(row.get("market") or detect_market(code).market)
            df = _default_loader(code, market)
            if df is None or len(df) < 60:
                out.append({"code": code, "name": name, "error": "K线不足", "action": "HOLD",
                            "action_label": "持有", "action_emoji": "🟡"})
                continue
            res = eng.analyze(code, name, df)
            if res.plan is None:
                out.append({"code": code, "name": name, "error": "无法生成计划", "action": "HOLD",
                            "action_label": "持有", "action_emoji": "🟡"})
                continue
            out.append(_item_from_plan(row, res, zones.get(code)))
        except Exception as e:  # noqa: BLE001
            log.warning("持仓量化失败 %s: %s", code, e)
            out.append({"code": code, "name": name, "error": str(e)[:200], "action": "HOLD",
                        "action_label": "持有", "action_emoji": "🟡"})
    return out


def quant_to_text(items: list[dict]) -> str:
    lines = ["== 持仓量化（已持有，非新开仓） =="]
    for a in items:
        if a.get("error") and not a.get("stock_score"):
            lines.append(f"{a.get('code')} {a.get('name','')} | 分析失败: {a['error']}")
            continue
        lines.append(
            f"{a.get('action_emoji','')} {a.get('action_label')} {a.get('code')} {a.get('name','')} | "
            f"现价{a.get('current_price')} 盈亏{a.get('pnl_pct')}% | "
            f"个股{a.get('stock_score')}/机会{a.get('opportunity_score')} | "
            f"技术{a.get('tech_grade')} 信息{a.get('info_grade')} | "
            f"止损{a.get('stop_loss')}"
        )
        if a.get("note"):
            lines.append(f"  {a['note']}")
    return "\n".join(lines)


def quant_to_html(items: list[dict]) -> str:
    if not items:
        return "<p>暂无持仓量化</p>"
    rows = []
    for a in items:
        if a.get("error") and not a.get("stock_score"):
            rows.append(
                f"<tr><td>{a.get('code')}</td><td colspan='6' style='color:#b91c1c'>"
                f"失败: {a['error']}</td></tr>"
            )
            continue
        act = f"{a.get('action_emoji','')} {a.get('action_label')}"
        scores = f"{a.get('stock_score','—')}/{a.get('opportunity_score','—')}"
        grades = f"{a.get('tech_grade','—')} / {a.get('info_grade','—')}"
        note = a.get("note") or ""
        rows.append(
            f"<tr><td><b>{act}</b></td>"
            f"<td>{a.get('code')}<br><span style='color:#6b7280;font-size:12px'>{a.get('name','')}</span></td>"
            f"<td>{a.get('current_price','—')}</td>"
            f"<td>{a.get('pnl_pct','—')}%</td>"
            f"<td>{scores}</td>"
            f"<td>{grades}</td>"
            f"<td>{a.get('stop_loss','—')}<br><span style='color:#6b7280;font-size:12px'>{note}</span></td>"
            f"</tr>"
        )
    return (
        "<p style='color:#6b7280;font-size:12px'>每个交易日计算一次（已持有解读）。"
        "卖出=触及止损；减仓=风险或转空；可加仓=趋势仍在且回踩入场区。</p>"
        "<table><thead><tr><th>动作</th><th>代码·名称</th><th>现价</th>"
        "<th>盈亏%</th><th>个股/机会</th><th>技术/信息</th><th>止损·说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
