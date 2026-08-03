"""Deep-loss sell zone uses rebound-to-cost path (offline, mocked kline)."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from quant_trading_system.stock_analysis.sell_zone import analyze_sell_zone


def _fake_df(n=80, last_close=1.4, cost_proxy=3.5):
    # synthetic OHLCV ending at last_close
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.linspace(cost_proxy, last_close, n)
    df = pd.DataFrame({
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(n, 1e6),
    }, index=idx)
    return df


def test_deep_loss_zone_targets_cost():
    df = _fake_df()
    with patch(
        "quant_trading_system.stock_analysis.sell_zone.detect_market",
        return_value={"code": "002269", "market": "CN"},
    ), patch(
        "quant_trading_system.stock_analysis.sell_zone.fetch_kline",
        return_value=df,
    ), patch(
        "quant_trading_system.stock_analysis.sell_zone.add_all_indicators",
        side_effect=lambda d: d.assign(
            ma5=d["close"], ma10=d["close"], ma20=d["close"] * 1.02,
            ma60=d["close"] * 1.05,
            boll_upper=d["close"] * 1.08, boll_mid=d["close"] * 1.03,
            boll_lower=d["close"] * 0.95, atr=0.05,
        ),
    ):
        r = analyze_sell_zone({"code": "002269", "cost_price": 3.595, "name": "美邦"})
    assert "error" not in r
    assert r["pnl_pct"] < -20
    assert r.get("regime") == "deep_loss"
    assert r["zone_hi"] == round(3.595, 4) or abs(r["zone_hi"] - 3.595) < 0.01
    assert "回本" in r["zone_hi_label"] or "回本" in r["advice"]
