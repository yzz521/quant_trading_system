"""Bridge between assistant Holdings (SQLite) and framework Portfolio.

Does **not** auto-trade. Use to:
- seed a Portfolio for paper/what-if from real holdings
- export Portfolio open positions into holdings-shaped dicts for the UI
"""
from __future__ import annotations

from typing import Optional

from ..portfolio import Portfolio, Position


def holdings_rows_to_positions(
    rows: list[dict],
    last_prices: Optional[dict[str, float]] = None,
) -> list[dict]:
    """Normalize SQLite/YAML rows to a common schema."""
    last_prices = last_prices or {}
    out = []
    for r in rows:
        code = str(r.get("code", "")).strip()
        if not code:
            continue
        qty = float(r.get("quantity") or 0)
        cost = float(r.get("cost_price") or 0)
        px = float(last_prices.get(code) or cost)
        out.append({
            "code": code,
            "symbol": code,
            "name": r.get("name") or code,
            "market": r.get("market", "CN"),
            "quantity": qty,
            "avg_price": cost,
            "cost_price": cost,
            "last_price": px,
            "buy_date": r.get("buy_date", ""),
            "market_value": qty * px,
            "unrealized_pnl": (px - cost) * qty if qty else 0.0,
        })
    return out


def apply_holdings_to_portfolio(
    portfolio: Portfolio,
    rows: list[dict],
    last_prices: Optional[dict[str, float]] = None,
    *,
    replace: bool = True,
) -> Portfolio:
    """Load holdings into ``portfolio.positions`` (paper / research only).

    Cash is left unchanged unless ``replace`` clears positions first.
    T+1: imported shares are treated as **settled** (frozen_quantity=0).
    """
    last_prices = last_prices or {}
    if replace:
        portfolio.positions.clear()
    for item in holdings_rows_to_positions(rows, last_prices):
        sym = item["symbol"]
        pos = portfolio.positions.get(sym) or Position(symbol=sym)
        pos.quantity = item["quantity"]
        pos.avg_price = item["avg_price"]
        pos.last_price = item["last_price"]
        pos.frozen_quantity = 0.0
        portfolio.positions[sym] = pos
    return portfolio


def portfolio_to_holdings_rows(portfolio: Portfolio, market: str = "CN") -> list[dict]:
    """Export open portfolio legs as holdings-compatible dicts."""
    rows = []
    for sym, p in portfolio.positions.items():
        if not p.is_open or p.quantity <= 0:
            continue
        rows.append({
            "code": sym,
            "name": sym,
            "market": market,
            "cost_price": float(p.avg_price),
            "quantity": int(abs(p.quantity)),
            "buy_date": "",
        })
    return rows


def snapshot_compare(
    holdings_rows: list[dict],
    portfolio: Portfolio,
    last_prices: Optional[dict[str, float]] = None,
) -> dict:
    """Diff quantities by code/symbol for reconciliation UI."""
    last_prices = last_prices or {}
    hmap = {str(r["code"]): float(r.get("quantity") or 0) for r in holdings_rows}
    pmap = {
        s: float(p.quantity)
        for s, p in portfolio.positions.items()
        if p.is_open
    }
    codes = sorted(set(hmap) | set(pmap))
    diffs = []
    for c in codes:
        hq, pq = hmap.get(c, 0.0), pmap.get(c, 0.0)
        if abs(hq - pq) > 1e-6:
            diffs.append({"code": c, "holdings_qty": hq, "portfolio_qty": pq})
    return {"ok": not diffs, "diffs": diffs, "n_holdings": len(hmap), "n_portfolio": len(pmap)}
