"""Holdings → framework risk snapshot (research only, no orders)."""
from __future__ import annotations

from typing import Optional

from ..portfolio import Portfolio
from ..risk import RiskManager
from .holdings_bridge import apply_holdings_to_portfolio, holdings_rows_to_positions


def diagnose_holdings(
    rows: list[dict],
    last_prices: Optional[dict[str, float]] = None,
    *,
    capital: float = 1_000_000.0,
    max_position_pct: float = 0.25,
    max_exposure: float = 1.0,
    max_positions: int = 20,
) -> dict:
    """Return portfolio-level and per-symbol risk diagnostics."""
    last_prices = last_prices or {}
    pf = Portfolio(capital, t1_enabled=True)
    apply_holdings_to_portfolio(pf, rows, last_prices, replace=True)

    # mark last prices
    for sym, pos in pf.positions.items():
        if sym in last_prices:
            pos.last_price = float(last_prices[sym])
        elif pos.last_price <= 0 and pos.avg_price > 0:
            pos.last_price = pos.avg_price

    equity = pf.equity
    gross = sum(abs(p.market_value) for p in pf.positions.values() if p.is_open)
    exposure = gross / equity if equity > 0 else 0.0
    n_open = sum(1 for p in pf.positions.values() if p.is_open)

    alerts: list[str] = []
    if exposure > max_exposure:
        alerts.append(f"总暴露 {exposure:.1%} 超过上限 {max_exposure:.0%}")
    if n_open > max_positions:
        alerts.append(f"持仓只数 {n_open} 超过上限 {max_positions}")

    per_symbol = []
    for sym, pos in sorted(pf.positions.items()):
        if not pos.is_open:
            continue
        weight = abs(pos.market_value) / equity if equity > 0 else 0.0
        row = {
            "code": sym,
            "quantity": pos.quantity,
            "available": pos.available_quantity,
            "frozen": pos.frozen_quantity,
            "avg_price": pos.avg_price,
            "last_price": pos.last_price,
            "market_value": round(pos.market_value, 2),
            "weight": round(weight, 4),
            "unrealized_pnl": round(pos.unrealized_pnl, 2),
            "over_weight": weight > max_position_pct + 1e-9,
        }
        if row["over_weight"]:
            alerts.append(f"{sym} 权重 {weight:.1%} > 单票上限 {max_position_pct:.0%}")
        per_symbol.append(row)

    return {
        "equity": round(equity, 2),
        "cash": round(pf.cash, 2),
        "gross_exposure": round(exposure, 4),
        "n_positions": n_open,
        "max_position_pct": max_position_pct,
        "max_exposure": max_exposure,
        "alerts": alerts,
        "ok": len(alerts) == 0,
        "positions": per_symbol,
    }
