from quant_trading_system.portfolio import Portfolio
from quant_trading_system.stock_analysis.holdings_bridge import (
    apply_holdings_to_portfolio,
    portfolio_to_holdings_rows,
    snapshot_compare,
)


def test_roundtrip_qty():
    rows = [
        {"code": "600000", "name": "浦发", "cost_price": 10.0, "quantity": 1000, "market": "CN"},
    ]
    pf = Portfolio(1_000_000, t1_enabled=True)
    apply_holdings_to_portfolio(pf, rows, {"600000": 10.5})
    assert pf.positions["600000"].quantity == 1000
    assert pf.positions["600000"].frozen_quantity == 0
    assert pf.available("600000") == 1000
    back = portfolio_to_holdings_rows(pf)
    assert back[0]["quantity"] == 1000
    assert snapshot_compare(rows, pf)["ok"] is True


def test_compare_detects_diff():
    pf = Portfolio(1_000_000, t1_enabled=False)
    apply_holdings_to_portfolio(pf, [{"code": "A", "cost_price": 1, "quantity": 100}])
    diff = snapshot_compare([{"code": "A", "quantity": 50}], pf)
    assert diff["ok"] is False
