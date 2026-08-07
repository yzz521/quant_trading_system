"""Build a backtest universe from scanner hits or holdings."""
from __future__ import annotations

from typing import Any, Iterable


def symbols_from_scan_hits(hits: Iterable[Any], limit: int = 50) -> list[str]:
    """Extract codes from StockScanner hit objects or dicts."""
    out: list[str] = []
    for h in hits:
        code = getattr(h, "code", None) or (h.get("code") if isinstance(h, dict) else None)
        if code and code not in out:
            out.append(str(code))
        if len(out) >= limit:
            break
    return out


def symbols_from_holdings(rows: list[dict], market: str | None = "CN") -> list[str]:
    out = []
    for r in rows:
        if market and r.get("market", "CN") != market:
            continue
        c = str(r.get("code", "")).strip()
        if c and c not in out:
            out.append(c)
    return out


def make_universe(
    *,
    scan_hits: Iterable[Any] | None = None,
    holdings_rows: list[dict] | None = None,
    extra: list[str] | None = None,
    limit: int = 50,
) -> list[str]:
    """Merge scan hits + holdings + extra symbols (deduped, order preserved)."""
    out: list[str] = []
    if scan_hits is not None:
        for s in symbols_from_scan_hits(scan_hits, limit=limit):
            if s not in out:
                out.append(s)
    if holdings_rows is not None:
        for s in symbols_from_holdings(holdings_rows):
            if s not in out:
                out.append(s)
    if extra:
        for s in extra:
            s = str(s).strip()
            if s and s not in out:
                out.append(s)
    return out[:limit]
