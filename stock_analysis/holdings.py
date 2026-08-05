"""Holdings manager — track real positions and compute live PnL.

Positions are stored in a local SQLite database ``config/holdings.db``
(code/name/market/cost_price/quantity/buy_date). The Streamlit holdings
dashboard edits this database directly — no more hand-editing YAML.

If an old ``config/holdings.yaml`` is found on first use and the database is
empty, its positions are imported automatically (the YAML file is left as a
backup). ``compute_pnl`` fetches the latest close for each holding and returns
per-position PnL plus a summary (total cost / value / pnl / pct).

Used by:
  * the scheduler — to inject a "我的持仓" block into each market's push
  * examples/my_holdings.py — a standalone "show me my positions" entry point
  * the dashboard — the holdings management page
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from ..utils import get_logger, load_yaml
from .data_fetcher import detect_market, fetch_kline

log = get_logger("Holdings")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    market     TEXT NOT NULL DEFAULT 'CN',
    cost_price REAL NOT NULL DEFAULT 0,
    quantity   INTEGER NOT NULL DEFAULT 0,
    buy_date   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS account (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    total_capital     REAL NOT NULL DEFAULT 0,
    max_position_pct  REAL NOT NULL DEFAULT 0.30
);
"""

_COLUMNS = ("code", "name", "market", "cost_price", "quantity", "buy_date")


def _row_to_pos(row: sqlite3.Row) -> dict:
    return {c: row[c] for c in _COLUMNS}


class Holdings:
    def __init__(self, config_path: str = "config/holdings.yaml") -> None:
        """Open the holdings DB next to the config file.

        For backwards compatibility ``config_path`` may still point at the old
        YAML file — the database is then created as ``<dir>/holdings.db`` and
        any existing YAML data is imported automatically on first use.
        """
        cfg = Path(config_path)
        self.db_path = cfg.with_name("holdings.db")
        self._yaml_path = cfg if cfg.suffix.lower() == ".yaml" \
            else cfg.with_name("holdings.yaml")
        self._ensure_schema()
        self.reload()
        self._migrate_from_yaml_if_needed()

    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO account(id, total_capital, max_position_pct) VALUES (1, 0, 0.30)"
            )

    def _migrate_from_yaml_if_needed(self) -> None:
        """One-time import: if the DB is empty but a holdings.yaml exists,
        move its positions into SQLite (YAML is left untouched as a backup)."""
        try:
            if self._yaml_path.exists() and self.is_empty():
                data = load_yaml(str(self._yaml_path)) or {}
                rows = data.get("holdings", []) or []
                if rows:
                    log.info("从 %s 迁移 %d 条持仓到 %s",
                             self._yaml_path, len(rows), self.db_path)
                    self.replace_all(rows)
        except Exception as e:  # noqa: BLE001
            log.warning("持仓自动迁移失败（可忽略）: %s", e)

    def reload(self) -> None:
        """Reload positions from the SQLite DB."""
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM holdings ORDER BY rowid")
            self.positions = [_row_to_pos(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ #
    def all(self) -> list[dict]:
        return list(self.positions)

    def by_market(self, market: str) -> list[dict]:
        return [p for p in self.positions if p.get("market", "CN") == market]

    def is_empty(self) -> bool:
        return not self.positions

    # ---- 资金账户（本地，不入库 git）-------------------------------- #
    def get_account(self) -> dict:
        """Return total_capital / max_position_pct. total_capital<=0 means unset."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT total_capital, max_position_pct FROM account WHERE id=1"
            ).fetchone()
        if row is None:
            return {"total_capital": 0.0, "max_position_pct": 0.30}
        return {
            "total_capital": float(row["total_capital"] or 0),
            "max_position_pct": float(row["max_position_pct"] or 0.30),
        }

    def set_account(self, total_capital: Optional[float] = None,
                    max_position_pct: Optional[float] = None) -> dict:
        acc = self.get_account()
        if total_capital is not None:
            acc["total_capital"] = max(0.0, float(total_capital))
        if max_position_pct is not None:
            pct = float(max_position_pct)
            if pct > 1.0:  # allow UI input as 30 meaning 30%
                pct = pct / 100.0
            acc["max_position_pct"] = min(max(pct, 0.05), 1.0)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO account(id, total_capital, max_position_pct) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "total_capital=excluded.total_capital, "
                "max_position_pct=excluded.max_position_pct",
                (acc["total_capital"], acc["max_position_pct"]),
            )
        return acc

    def set_total_capital(self, capital: float) -> dict:
        return self.set_account(total_capital=capital)

    def invested_cost(self) -> float:
        """Sum of cost_price * quantity (conservative occupancy)."""
        total = 0.0
        for p in self.all():
            total += float(p.get("cost_price") or 0) * float(p.get("quantity") or 0)
        return round(total, 2)

    def available_cash(self) -> Optional[float]:
        """None if total_capital unset; else max(0, total - invested_cost)."""
        acc = self.get_account()
        cap = float(acc.get("total_capital") or 0)
        if cap <= 0:
            return None
        return round(max(0.0, cap - self.invested_cost()), 2)

    def capital_snapshot(self) -> Optional[dict]:
        """Summary for UI/email; None if capital not configured."""
        acc = self.get_account()
        cap = float(acc.get("total_capital") or 0)
        if cap <= 0:
            return None
        invested = self.invested_cost()
        avail = max(0.0, cap - invested)
        return {
            "total_capital": round(cap, 2),
            "invested_cost": invested,
            "available_cash": round(avail, 2),
            "max_position_pct": float(acc.get("max_position_pct") or 0.30),
            "utilization_pct": round(invested / cap * 100, 1) if cap else 0.0,
        }

    # ---- CRUD (used by the holdings dashboard) ----------------------- #
    def add(self, code: str, name: str = "", market: str = "CN",
            cost_price: float = 0.0, quantity: int = 0, buy_date: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO holdings(code,name,market,cost_price,quantity,buy_date)"
                " VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET "
                "name=excluded.name, market=excluded.market, "
                "cost_price=excluded.cost_price, quantity=excluded.quantity, "
                "buy_date=excluded.buy_date",
                (code, name, market, float(cost_price), int(quantity), buy_date),
            )
        self.reload()

    def update(self, code: str, name: Optional[str] = None,
               market: Optional[str] = None, cost_price: Optional[float] = None,
               quantity: Optional[int] = None, buy_date: Optional[str] = None) -> None:
        fields = {}
        if name is not None:
            fields["name"] = name
        if market is not None:
            fields["market"] = market
        if cost_price is not None:
            fields["cost_price"] = float(cost_price)
        if quantity is not None:
            fields["quantity"] = int(quantity)
        if buy_date is not None:
            fields["buy_date"] = buy_date
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(f"UPDATE holdings SET {sets} WHERE code=?",
                         (*fields.values(), code))
        self.reload()

    def delete(self, codes: list[str]) -> None:
        codes = [c for c in codes if c]
        if not codes:
            return
        marks = ",".join("?" * len(codes))
        with self._conn() as conn:
            conn.execute(f"DELETE FROM holdings WHERE code IN ({marks})", codes)
        self.reload()

    def apply_sell(self, code: str, quantity: int) -> str:
        """Reduce position by ``quantity`` shares. Delete row if remaining <= 0.

        Returns a short human message. Does not know about broker fills —
        caller must pass the sold quantity explicitly (manual form or parsed trade).
        """
        code = str(code).strip().upper()
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("卖出数量必须大于 0")
        rows = {p["code"].upper(): p for p in self.all()}
        if code not in rows:
            raise ValueError(f"持仓中无 {code}")
        cur = int(float(rows[code]["quantity"]))
        if qty > cur:
            raise ValueError(f"卖出数量 {qty} 大于持仓 {cur}")
        left = cur - qty
        if left <= 0:
            self.delete([code])
            return f"已清仓 {code}（卖出 {qty} 股）"
        self.update(code, quantity=left)
        return f"{code} 卖出 {qty} 股，剩余 {left} 股"


    def replace_all(self, positions: list[dict]) -> None:
        """Replace the whole table (used for migration / bulk edits)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM holdings")
            for p in positions:
                conn.execute(
                    "INSERT INTO holdings(code,name,market,cost_price,quantity,buy_date)"
                    " VALUES(?,?,?,?,?,?)",
                    (str(p["code"]), p.get("name", ""), p.get("market", "CN"),
                     float(p["cost_price"]), int(float(p["quantity"])),
                     p.get("buy_date", "")),
                )
        self.reload()

    # ------------------------------------------------------------------ #
    def compute_pnl(self, market: Optional[str] = None) -> tuple[list[dict], dict]:
        """Compute live PnL. Returns (positions_with_pnl, summary).

        Each position gains: current_price, market_value, pnl, pnl_pct, hold_days.
        Network failures degrade gracefully — the position keeps last known data
        with pnl marked None.

        Positions are re-read from the database before computing, so changes
        made externally (e.g. via the holdings dashboard) take effect
        immediately.
        """
        self.reload()
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
