from quant_trading_system.stock_analysis.risk_diagnosis import diagnose_holdings

def test_overweight_alert():
    rows = [{"code": "A", "cost_price": 10, "quantity": 50_000}]
    r = diagnose_holdings(rows, {"A": 10.0}, capital=1_000_000, max_position_pct=0.25)
    assert r["ok"] is False
    assert any("A" in a for a in r["alerts"])

def test_ok_small_position():
    rows = [{"code": "A", "cost_price": 10, "quantity": 1000}]
    r = diagnose_holdings(rows, {"A": 10.0}, capital=1_000_000, max_position_pct=0.25)
    assert r["ok"] is True
