"""Holdings: SQLite CRUD + account/capital logic (offline)."""
from __future__ import annotations

import pytest

from quant_trading_system.stock_analysis.holdings import Holdings


@pytest.fixture
def holdings(tmp_path):
    """A Holdings instance backed by a temp SQLite DB (no real config)."""
    db = tmp_path / "holdings.yaml"  # 同名触发 .db 落在 tmp_path
    h = Holdings(str(db))
    yield h


def _add(h, code="600519", name="贵州茅台", market="CN",
         cost_price=100.0, quantity=10, buy_date="2026-01-05"):
    h.add(code, name=name, market=market, cost_price=cost_price,
          quantity=quantity, buy_date=buy_date)


def test_add_and_read(holdings):
    _add(holdings)
    assert holdings.is_empty() is False
    pos = holdings.all()[0]
    assert pos["code"] == "600519"
    assert pos["name"] == "贵州茅台"
    assert pos["cost_price"] == 100.0
    assert pos["quantity"] == 10


def test_add_same_code_updates(holdings):
    _add(holdings, quantity=10)
    _add(holdings, quantity=20)  # 同 code → 更新不新增
    assert len(holdings.all()) == 1
    assert holdings.all()[0]["quantity"] == 20


def test_update_partial(holdings):
    _add(holdings)
    holdings.update("600519", quantity=15)
    assert holdings.all()[0]["quantity"] == 15
    # 未传字段应保留
    assert holdings.all()[0]["cost_price"] == 100.0


def test_by_market(holdings):
    _add(holdings, market="CN")
    _add(holdings, code="AAPL", name="苹果", market="US")
    assert len(holdings.by_market("CN")) == 1
    assert len(holdings.by_market("US")) == 1


def test_delete(holdings):
    _add(holdings)
    holdings.delete(["600519"])
    assert holdings.is_empty()


def test_apply_sell_partial(holdings):
    _add(holdings, quantity=10)
    msg = holdings.apply_sell("600519", 4)
    assert "剩余 6" in msg
    assert holdings.all()[0]["quantity"] == 6


def test_apply_sell_clear(holdings):
    _add(holdings, quantity=10)
    msg = holdings.apply_sell("600519", 10)
    assert "清仓" in msg
    assert holdings.is_empty()


def test_apply_sell_over_qty_raises(holdings):
    _add(holdings, quantity=10)
    with pytest.raises(ValueError):
        holdings.apply_sell("600519", 11)


def test_apply_sell_missing_code_raises(holdings):
    with pytest.raises(ValueError):
        holdings.apply_sell("999999", 1)


def test_invested_cost_and_cash(holdings):
    _add(holdings, cost_price=100.0, quantity=10)
    assert holdings.invested_cost() == 1000.0
    # 未设总资金 → available_cash 为 None
    assert holdings.available_cash() is None


def test_set_account_capital(holdings):
    _add(holdings, cost_price=100.0, quantity=10)
    holdings.set_total_capital(5000)
    acc = holdings.get_account()
    assert acc["total_capital"] == 5000.0
    assert holdings.available_cash() == 4000.0


def test_account_pct_30_means_30pct(holdings):
    # UI 里输入 30 表示 30%
    holdings.set_account(max_position_pct=30)
    assert holdings.get_account()["max_position_pct"] == 0.30


def test_capital_snapshot_none_when_unset(holdings):
    _add(holdings)
    assert holdings.capital_snapshot() is None
