"""可买性分析：在总资金约束下给推荐标的打标。

未设置 total_capital 时 annotate_* 返回原列表且不添加 buy_tag（保持现状）。
"""
from __future__ import annotations

from typing import Any, Optional

from .holdings import Holdings


def lot_size_for_market(market: str) -> int:
    m = (market or "CN").upper()
    if m in ("CN", "A", "SH", "SZ"):
        return 100
    return 1  # HK / US


def _held_map(holdings: list[dict]) -> dict[str, dict]:
    out = {}
    for h in holdings or []:
        code = str(h.get("code") or "").strip().upper()
        if code:
            out[code] = h
    return out


def _invested_in(code: str, held: dict[str, dict]) -> float:
    h = held.get(code.upper())
    if not h:
        return 0.0
    return float(h.get("cost_price") or 0) * float(h.get("quantity") or 0)


def analyze_buy_power(
    code: str,
    price: float,
    *,
    market: str = "CN",
    name: str = "",
    total_capital: float,
    available_cash: float,
    max_position_pct: float = 0.30,
    holdings: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Return tag fields for one symbol."""
    code = str(code).strip().upper()
    price = float(price or 0)
    lot = lot_size_for_market(market)
    held = _held_map(holdings or [])
    invested = _invested_in(code, held)
    cap_limit = total_capital * max_position_pct
    room = max(0.0, cap_limit - invested)
    one_lot_cost = price * lot if price > 0 else float("inf")

    result: dict[str, Any] = {
        "code": code,
        "name": name,
        "market": market,
        "price": price,
        "lot": lot,
        "one_lot_cost": round(one_lot_cost, 2) if price > 0 else None,
        "invested": round(invested, 2),
        "room": round(room, 2),
        "buy_tag": "",
        "buy_label": "",
        "max_shares": 0,
        "need_amount": 0.0,
    }

    if code in held:
        result["buy_tag"] = "held"
        if invested >= cap_limit - 1e-6:
            result["buy_label"] = f"🔁 已持有且已达上限（投入约 {invested:.0f}）"
        else:
            result["buy_label"] = (
                f"🔁 已持有（投入约 {invested:.0f}，上限内剩余额度约 {room:.0f}）"
            )
        # optional add shares within room & cash
        budget = min(available_cash, room)
        if price > 0 and budget >= one_lot_cost:
            shares = int(budget // (price * lot)) * lot
            result["max_shares"] = shares
            result["need_amount"] = round(shares * price, 2)
            if shares > 0:
                result["buy_label"] += f"；若加仓最多约 {shares} 股需 {result['need_amount']:.0f}"
        return result

    if invested >= cap_limit - 1e-6:
        result["buy_tag"] = "capped"
        result["buy_label"] = f"🚫 已达仓位上限（{max_position_pct:.0%}）"
        return result

    if price <= 0:
        result["buy_tag"] = "unknown"
        result["buy_label"] = "❓ 无有效价格"
        return result

    if one_lot_cost > available_cash + 1e-6:
        result["buy_tag"] = "no_cash"
        result["buy_label"] = (
            f"⚠️ 资金不足（一手约 {one_lot_cost:,.0f}，可用 {available_cash:,.0f}）"
        )
        return result

    if one_lot_cost > room + 1e-6:
        result["buy_tag"] = "capped"
        result["buy_label"] = (
            f"🚫 一手将超单票上限（一手约 {one_lot_cost:,.0f}，剩余额度 {room:,.0f}）"
        )
        return result

    budget = min(available_cash, room)
    shares = int(budget // (price * lot)) * lot
    if shares < lot:
        result["buy_tag"] = "no_cash"
        result["buy_label"] = (
            f"⚠️ 资金不足（一手约 {one_lot_cost:,.0f}，可用 {available_cash:,.0f}）"
        )
        return result

    need = shares * price
    result["buy_tag"] = "ok"
    result["max_shares"] = shares
    result["need_amount"] = round(need, 2)
    result["buy_label"] = f"✅ 可买 {shares} 股，约需 {need:,.0f}"
    return result


def annotate_list(
    items: list[dict],
    *,
    holdings_mgr: Holdings,
    price_key: str = "price",
    code_key: str = "code",
    name_key: str = "name",
    market_key: str = "market",
    default_market: str = "CN",
) -> tuple[Optional[dict], list[dict]]:
    """Annotate items in-place-like copies. Returns (capital_snapshot|None, items)."""
    snap = holdings_mgr.capital_snapshot()
    if snap is None:
        return None, list(items)

    held_rows = holdings_mgr.all()
    out = []
    for it in items:
        row = dict(it)
        code = str(row.get(code_key) or "").strip()
        price = row.get(price_key)
        if price is None:
            price = row.get("close") or row.get("current_price") or 0
        market = row.get(market_key) or default_market
        bp = analyze_buy_power(
            code,
            float(price or 0),
            market=str(market),
            name=str(row.get(name_key) or ""),
            total_capital=snap["total_capital"],
            available_cash=snap["available_cash"],
            max_position_pct=snap["max_position_pct"],
            holdings=held_rows,
        )
        row["buy_tag"] = bp["buy_tag"]
        row["buy_label"] = bp["buy_label"]
        row["buy_max_shares"] = bp["max_shares"]
        row["buy_need_amount"] = bp["need_amount"]
        out.append(row)
    return snap, out


def partition_annotated(items: list[dict], max_ok: int = 10) -> dict[str, list]:
    ok, held, no_cash, capped, other = [], [], [], [], []
    for it in items:
        tag = it.get("buy_tag") or ""
        if tag == "ok":
            ok.append(it)
        elif tag == "held":
            held.append(it)
        elif tag == "no_cash":
            no_cash.append(it)
        elif tag == "capped":
            capped.append(it)
        else:
            other.append(it)
    return {
        "ok": ok[:max_ok],
        "ok_extra": ok[max_ok:],
        "held": held,
        "no_cash": no_cash,
        "capped": capped,
        "other": other,
    }
