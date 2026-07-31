"""Holdings manager — track real positions and compute live PnL.

Positions are stored in ``config/holdings.yaml`` (code/name/market/cost_price/
quantity/buy_date). ``compute_pnl`` fetches the latest close for each holding
and returns per-position PnL plus a summary (total cost / value / pnl / pct).

Used by:
  * the scheduler — to inject a "我的持仓" block into each market's push
  * examples/my_holdings.py — a standalone "show me my positions" entry point
  * the dashboard — a "我的持仓" tab
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from ..utils import get_logger, load_yaml
from .data_fetcher import detect_market, fetch_kline, fetch_name, MarketInfo

log = get_logger("Holdings")


class Holdings:
    def __init__(self, config_path: str = "config/holdings.yaml") -> None:
        self.cfg = load_yaml(config_path) or {}
        self.positions = self.cfg.get("holdings", []) or []

    def all(self) -> list[dict]:
        return list(self.positions)

    def by_market(self, market: str) -> list[dict]:
        return [p for p in self.positions if p.get("market", "CN") == market]

    def is_empty(self) -> bool:
        return not self.positions

    # ------------------------------------------------------------------ #
    def compute_pnl(self, market: Optional[str] = None) -> tuple[list[dict], dict]:
        """Compute live PnL. Returns (positions_with_pnl, summary).

        Each position gains: current_price, market_value, pnl, pnl_pct, hold_days.
        Network failures degrade gracefully — the position keeps last known data
        with pnl marked None.
        """
        positions = self.by_market(market) if market else self.all()
        results: list[dict] = []
        total_cost = total_value = 0.0

        for p in positions:
            code = p["code"]
            cost_price = float(p["cost_price"])
            qty = float(p["quantity"])
            cost = cost_price * qty
            entry = {
                "code": code,
                "name": p.get("name", code),
                "market": p.get("market", "CN"),
                "cost_price": cost_price,
                "quantity": qty,
                "buy_date": p.get("buy_date", ""),
            }
            try:
                info = detect_market(code)
                df = fetch_kline(info, days=10)
                if not df.empty:
                    price = float(df["close"].iloc[-1])
                    value = price * qty
                    pnl = value - cost
                    entry.update({
                        "current_price": round(price, 4),
                        "market_value": round(value, 2),
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / cost * 100, 2) if cost else 0.0,
                        "hold_days": self._hold_days(p.get("buy_date")),
                    })
                    total_cost += cost
                    total_value += value
                else:
                    entry.update({"current_price": None, "pnl": None, "pnl_pct": None})
            except Exception as e:  # noqa: BLE001
                log.debug("持仓 %s 取价失败: %s", code, e)
                entry.update({"current_price": None, "pnl": None, "pnl_pct": None})

            results.append(entry)

        summary = {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_value - total_cost, 2),
            "total_pnl_pct": round((total_value - total_cost) / total_cost * 100, 2)
                             if total_cost else 0.0,
            "count": len(results),
        }
        return results, summary

    @staticmethod
    def _hold_days(buy_date: str) -> int:
        if not buy_date:
            return 0
        try:
            d = pd.to_datetime(buy_date).date()
            return max(0, (date.today() - d).days)
        except Exception:  # noqa: BLE001
            return 0
