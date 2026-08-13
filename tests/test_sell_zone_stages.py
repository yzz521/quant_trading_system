from unittest.mock import patch

import numpy as np
import pandas as pd
from quant_trading_system.stock_analysis.sell_zone import analyze_sell_zone


def test_deep_loss_has_stages():
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.linspace(3.5, 1.4, n)
    df = pd.DataFrame({
        "open": close, "high": close * 1.02, "low": close * 0.98,
        "close": close, "volume": 1e6,
    }, index=idx)
    with patch("quant_trading_system.stock_analysis.sell_zone.detect_market", return_value={"code": "X", "market": "CN"}), \
         patch("quant_trading_system.stock_analysis.sell_zone.fetch_kline", return_value=df), \
         patch("quant_trading_system.stock_analysis.sell_zone.add_all_indicators",
               side_effect=lambda d: d.assign(
                   ma5=d["close"], ma10=d["close"], ma20=d["close"] * 1.05,
                   ma60=d["close"] * 1.15,
                   boll_upper=d["close"] * 1.1, boll_mid=d["close"] * 1.08,
                   boll_lower=d["close"] * 0.9, atr=0.05)):
        r = analyze_sell_zone({"code": "X", "cost_price": 3.595})
    assert r.get("regime") == "deep_loss"
    assert r.get("stage1_lo") is not None
    assert r.get("stage2_price") == 3.595
    assert "第一目标" in r["advice"]
