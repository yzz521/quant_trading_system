from quant_trading_system.stock_analysis.universe import make_universe, symbols_from_scan_hits

def test_make_universe_dedup():
    hits = [{"code": "600000"}, {"code": "000001"}]
    u = make_universe(scan_hits=hits, holdings_rows=[{"code": "600000", "market": "CN"}], extra=["000001", "600036"])
    assert u[0] == "600000"
    assert u.count("600000") == 1
    assert "600036" in u
