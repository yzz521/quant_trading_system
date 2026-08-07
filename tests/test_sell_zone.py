"""sell_zone pure-logic helpers (no network)."""
from __future__ import annotations

from quant_trading_system.stock_analysis.sell_zone import analyze_sell_zone


def test_missing_code_returns_error():
    r = analyze_sell_zone({"code": "", "cost_price": 10.0})
    assert "error" in r


def test_invalid_fetch_degrades_gracefully():
    # Unlikely real symbol + will fail fetch without network or return error
    r = analyze_sell_zone({"code": "ZZZZZZ_NOT_A_REAL_SYMBOL", "cost_price": 1.0})
    assert r["code"] == "ZZZZZZ_NOT_A_REAL_SYMBOL"
    # Either error or empty data path
    assert "error" in r or "advice" in r or "current_price" in r
