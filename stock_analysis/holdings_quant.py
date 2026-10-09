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

import pandas as pd

from ..utils import get_logger
from .data_fetcher import detect_market, fetch_kline, fetch_live_prices
from .indicators import add_all_indicators
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


def now_stamp() -> str:
    """数据时间戳（北京时间 ``YYYY-MM-DD HH:MM``），随结果一起落盘/展示。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def _holding_loader(code: str, market: str = "CN") -> Optional[pd.DataFrame]:
    """持仓量化专用 K 线加载器：末根必须是**当日**（含盘中未收盘的那根）。

    为什么不能直接复用批量扫描的 ``_default_loader``：它对 A 股走新浪日 K
    （``quotes.sina.cn`` 的 getKLineData），该接口**盘中不返回当日 bar**，
    末根停在前一交易日。后果是持仓量化的「现价」变成昨收，而「盈亏%」来自
    当日口径的 sell_zone —— 同一行里两个数字互相打架；均线/入场区间/止损
    等技术位也整体滞后一天。

    持仓只有几只且串行调用，可以走「带当日 bar 的前复权源」（同花顺 → akshare
    qfq）。批量扫描几千只、并发且有配额考量，继续用 ``_default_loader``，
    两条路径互不影响。
    """
    try:
        info = detect_market(code)
        raw = fetch_kline(info, days=250)
        if raw is not None and len(raw) >= 60:
            return add_all_indicators(raw)
        log.debug("持仓量化 %s 日K不足（%s 行），回退批量源", code,
                  0 if raw is None else len(raw))
    except Exception as e:  # noqa: BLE001
        log.debug("持仓量化 %s 走前复权源失败，回退批量源: %s", code, e)
    return _default_loader(code, market)


def cached_block(market: str, date_str: str, path: Optional[Path] = None) -> dict:
    """当日缓存块（含 ``at`` 写入时间）；无当日缓存返回 ``{}``。"""
    block = load_cache(path).get(market) or {}
    if block.get("date") == date_str and isinstance(block.get("items"), list):
        return block
    return {}


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
    block = cached_block(market, date_str, path)
    return block["items"] if block else None


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
    live_price: Optional[float] = None,
) -> dict:
    """Map a new-entry TradingPlan onto an existing position.

    Stop / 重大风险 / 趋势转空优先于「RR 不够所以 AVOID」。

    价格口径（重要）：现价按
    ``live_price`` → ``position["current_price"]``（实时快照）→
    ``plan.current_price``（K 线末根）→ ``zone["current_price"]`` 依次取值。
    只要现价与成本价同时可得，**盈亏% 就用同一个现价重算**，绝不出现
    「现价取日 K 末根（昨收）、盈亏% 取当日价」这种同行两个源的组合。
    """
    position = position or {}
    zone = zone or {}
    price = _num(
        live_price
        or position.get("current_price")
        or (getattr(plan, "current_price", None) if plan is not None and not isinstance(plan, dict)
            else (plan or {}).get("current_price") if isinstance(plan, dict) else None)
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

    cost = _num(position.get("cost_price")) or _num(zone.get("cost_price"))
    if price is not None and cost:
        # 现价与成本价都在手 → 用同一现价重算，保证同行价格/盈亏同源
        pnl = (price - cost) / cost * 100
    else:
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
        # 与 compute_pnl 同精度（4 位），否则 ETF 这类低价标的会出现
        # 持仓表 1.506 / 量化表 1.51 的"看起来不一样"
        "current_price": round(price, 4) if price is not None else None,
        # 关键位落盘，供实时盯盘快轨自动生成 price_cross 价位（慢轨算、快轨用）
        "zone_lo": zone.get("zone_lo"),
        "zone_hi": zone.get("zone_hi"),
        "zone_lo_label": zone.get("zone_lo_label"),
        "zone_hi_label": zone.get("zone_hi_label"),
        "stage1_lo": zone.get("stage1_lo"),
        "stage1_hi": zone.get("stage1_hi"),
        "stage2_price": zone.get("stage2_price"),
        "target_1": t1,
    }


def _item_from_plan(row: dict, res, zone: Optional[dict],
                    live_price: Optional[float] = None) -> dict:
    plan = res.plan if res is not None else None
    mapped = interpret_holding_action(plan, row, zone, live_price=live_price)
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
    prices: Optional[dict[str, float]] = None,
    loader=None,
) -> list[dict]:
    """逐只持仓跑机会引擎并映射为持有动作。单票失败不影响其余。

    Args:
        prices: 现价快照 ``{code: price}``。**强烈建议由调用方传入自己刚取到的
            那份实时价**（例如 ``compute_pnl`` 的结果），这样同一封邮件里
            「我的持仓」与「持仓量化」对同一只票必然同价；传 ``None`` 时本函数
            自行取一份实时价，传 ``{}`` 表示明确不覆盖（纯离线/单测）。
        loader: K 线加载器，默认 ``_holding_loader``（末根含当日）。注入用。
    """
    eng = engine or OpportunityEngine(
        fetch_news=fetch_news,
        regime_score=regime_score,
        sector_map=sector_map or {},
        sector_rank=sector_rank or [],
        account_equity=None,
    )
    load = loader or _holding_loader
    zones = zones or {}
    if prices is None:
        prices = fetch_live_prices([str(r.get("code") or "") for r in rows])
    stamp = now_stamp()
    out: list[dict] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        name = str(row.get("name") or code)
        try:
            market = str(row.get("market") or detect_market(code).market)
            df = load(code, market)
            if df is None or len(df) < 60:
                out.append({"code": code, "name": name, "error": "K线不足", "action": "HOLD",
                            "action_label": "持有", "action_emoji": "🟡", "as_of": stamp})
                continue
            res = eng.analyze(code, name, df)
            if res.plan is None:
                out.append({"code": code, "name": name, "error": "无法生成计划", "action": "HOLD",
                            "action_label": "持有", "action_emoji": "🟡", "as_of": stamp})
                continue
            item = _item_from_plan(row, res, zones.get(code),
                                   live_price=prices.get(code.upper()))
            item["as_of"] = stamp
            item["price_src"] = "live" if prices.get(code.upper()) else "kline"
            out.append(item)
        except Exception as e:  # noqa: BLE001
            log.warning("持仓量化失败 %s: %s", code, e)
            out.append({"code": code, "name": name, "error": str(e)[:200], "action": "HOLD",
                        "action_label": "持有", "action_emoji": "🟡", "as_of": stamp})
    return out


def apply_live_prices(items: list[dict], prices: Optional[dict[str, float]]) -> list[dict]:
    """用一份实时价快照刷新持仓量化条目的现价与盈亏%（原地改并返回同一列表）。

    持仓量化结果当天会缓存复用（可能上午 10:57 落盘），而「我的持仓」表每次
    推送都重新取价 —— 不刷新的话同一封邮件里两张表对同一只票会给出两个价格。
    调用方应传入**与持仓表同一份**的快照，保证同源同刻。

    只改 ``current_price`` / ``pnl_pct`` / ``as_of``；``stop_loss``、``zone_lo``
    等技术位是 K 线算出来的，不动。
    """
    if not prices:
        return items
    stamp = now_stamp()
    for it in items or []:
        code = str(it.get("code") or "").strip().upper()
        p = _num(prices.get(code)) if isinstance(prices, dict) else None
        if p is None or p <= 0:
            continue
        it["current_price"] = round(p, 4)
        cost = _num(it.get("cost_price"))
        if cost:
            it["pnl_pct"] = round((p - cost) / cost * 100, 2)
        it["as_of"] = stamp
        it["price_src"] = "live"
    return items


def quant_to_text(items: list[dict]) -> str:
    lines = ["== 持仓量化（已持有，非新开仓） =="]
    stamp = next((a.get("as_of") for a in items if a.get("as_of")), "")
    if stamp:
        lines.append(f"数据时间 {stamp}（现价与上方持仓表同源）")
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
    stamp = next((a.get("as_of") for a in items if a.get("as_of")), "")
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
        "卖出=触及止损；减仓=风险或转空；可加仓=趋势仍在且回踩入场区。"
        + (f"　数据时间 {stamp}（现价与上方持仓表同源）。" if stamp else "")
        + "</p>"
        "<table><thead><tr><th>动作</th><th>代码·名称</th><th>现价</th>"
        "<th>盈亏%</th><th>个股/机会</th><th>技术/信息</th><th>止损·说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


# --------------------------------------------------------------------------- #
# 组合级风控块
# --------------------------------------------------------------------------- #
def portfolio_risk_block(
    holdings: list[dict],
    *,
    total_equity: Optional[float] = None,
    sector_map: Optional[dict] = None,
    returns=None,
    equity_curve=None,
    limits=None,
) -> Optional[dict]:
    """把持仓表转成组合风控结论（可直接序列化进邮件/看板）。

    权重口径：有 ``total_equity`` 时用「市值 / 总资产」（含现金，与
    ``position_sizing.max_position_pct`` 同一把尺子）；否则退化为持仓内部相对权重。

    任何异常都吞掉返回 ``None``：组合风控是**附加**信息，不能因为它的数据问题
    让整封推送失败。
    """
    if not holdings:
        return None
    try:
        from .portfolio_risk import RiskLimits, assess_portfolio_risk, holdings_frame

        rows: list[dict] = []
        for h in holdings:
            code = str(h.get("code") or "").strip()
            if not code:
                continue
            mv = None
            try:
                qty = h.get("quantity")
                px = h.get("current_price") or h.get("cost_price")
                if qty is not None and px is not None:
                    mv = float(qty) * float(px)
            except (TypeError, ValueError):
                mv = None
            rows.append({
                "code": code,
                "name": h.get("name") or code,
                "market": h.get("market"),
                "industry": (sector_map or {}).get(code) or h.get("industry") or "未知",
                "market_value": mv,
            })
        if not rows:
            return None

        eq = None
        try:
            if total_equity is not None and float(total_equity) > 0:
                eq = float(total_equity)
        except (TypeError, ValueError):
            eq = None
        if eq:
            frame = holdings_frame(rows)
            if frame.empty:
                return None
            frame["weight"] = frame["market_value"] / eq
            payload = frame.to_dict("records")
        else:
            payload = rows

        rep = assess_portfolio_risk(
            payload, returns=returns, equity=equity_curve,
            limits=limits or RiskLimits(),
        )
        from .portfolio_risk import summarize as _summarize

        return {
            "verdict": rep.verdict,
            "summary": _summarize(rep),
            "report": rep.to_dict(),
            "breaches": list(rep.breaches),
            "actions": list(rep.actions),
            "brake": float(rep.drawdown.get("brake", 1.0) or 1.0),
            # 净值曲线充分度：熔断要按交易日累积，前 N 天依据必然偏薄。
            # 把「没触发」和「还没能力判断」区分开，否则沉默会被误读成安全。
            "equity_n": int(rep.drawdown.get("n_points") or 0),
            "equity_required": int(rep.drawdown.get("required") or 0),
            "equity_armed": bool(rep.drawdown.get("armed", False)),
            "as_of": now_stamp(),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("组合风控计算失败: %s", e)
        return None


def risk_block_to_text(block: Optional[dict]) -> str:
    """组合风控块的纯文本渲染（邮件用）。"""
    if not block:
        return ""
    return f"== 组合风控 ==\n{block.get('summary', '')}"


def risk_block_to_html(block: Optional[dict]) -> str:
    """组合风控块的 HTML 渲染（邮件用）。"""
    if not block:
        return "<p>暂无组合风控</p>"
    rep = block.get("report") or {}
    conc = rep.get("concentration") or {}
    corr = rep.get("correlation") or {}
    var = rep.get("var") or {}
    dd = rep.get("drawdown") or {}

    def _pct(v, digits=1):
        return "—" if v is None else f"{float(v) * 100:.{digits}f}%"

    def _num(v, digits=2):
        return "—" if v is None else f"{float(v):.{digits}f}"

    cards = [
        ("有效持仓数", _num(conc.get("effective_n"))),
        ("单票最大", _pct(conc.get("top1"))),
        ("前三大合计", _pct(conc.get("top3"))),
        ("平均两两相关", _num(corr.get("avg_corr"))),
        ("单日 VaR", _pct(var.get("var"), 2)),
        ("当前回撤", _pct(dd.get("current_drawdown"))),
        ("仓位系数", _pct(block.get("brake"), 0)),
        ("净值点数", f"{block.get('equity_n', 0)}/{block.get('equity_required', 0)}"),
    ]
    cells = "".join(
        f"<td style='padding:4px 10px;text-align:center'><div style='color:#6b7280;"
        f"font-size:12px'>{k}</div><b>{v}</b></td>" for k, v in cards
    )
    ind = rep.get("by_industry") or []
    ind_line = ""
    if ind:
        ind_line = ("<p style='font-size:12px;color:#374151'>行业暴露：" + "　".join(
            f"{r['industry']} {_pct(r['weight'])}" for r in ind[:6]) + "</p>")
    if not block.get("equity_armed", True):
        ind_line += (
            f"<p style='font-size:12px;color:#92400e'>ℹ️ 净值曲线仅 "
            f"{block.get('equity_n', 0)} 个交易日（需 ≥{block.get('equity_required', 0)}），"
            f"回撤熔断仍按现有曲线生效，但判断依据偏薄 —— 曲线按交易日自动累积。</p>"
        )
    warn = ""
    if block.get("breaches"):
        items = "".join(f"<li>{b}</li>" for b in block["breaches"])
        warn += f"<p style='color:#b91c1c;margin:6px 0 2px'><b>⚠️ 超限</b></p><ul>{items}</ul>"
    if block.get("actions"):
        items = "".join(f"<li>{a}</li>" for a in block["actions"])
        warn += f"<p style='color:#065f46;margin:6px 0 2px'><b>→ 建议</b></p><ul>{items}</ul>"
    return (
        f"<p style='color:#6b7280;font-size:12px'>结论：<b>{block.get('verdict')}</b>"
        f"　数据时间 {block.get('as_of', '')}</p>"
        f"<table><tbody><tr>{cells}</tr></tbody></table>"
        f"{ind_line}{warn}"
    )
