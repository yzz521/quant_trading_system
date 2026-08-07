from quant_trading_system.stock_analysis.buy_power import analyze_buy_power

def test_moutai_no_cash():
    r = analyze_buy_power(
        "600519", 1700.0, market="CN",
        total_capital=10000, available_cash=500, max_position_pct=0.3,
        holdings=[],
    )
    assert r["buy_tag"] == "no_cash"

def test_cheap_ok():
    r = analyze_buy_power(
        "000001", 10.0, market="CN",
        total_capital=10000, available_cash=5000, max_position_pct=0.3,
        holdings=[],
    )
    assert r["buy_tag"] == "ok"
    assert r["max_shares"] >= 100

def test_held():
    r = analyze_buy_power(
        "000001", 10.0, market="CN",
        total_capital=10000, available_cash=5000, max_position_pct=0.3,
        holdings=[{"code": "000001", "cost_price": 10, "quantity": 100}],
    )
    assert r["buy_tag"] == "held"

def test_full_deployed():
    r = analyze_buy_power(
        "000001", 10.0, market="CN",
        total_capital=10000, available_cash=0, max_position_pct=0.3,
        holdings=[],
    )
    assert r["buy_tag"] == "no_cash"
