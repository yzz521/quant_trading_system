
from quant_trading_system.stock_analysis.ai_summary import _pack_context

def test_pack_context_slim():
    ctx = _pack_context(
        market="CN",
        holdings=[{"code": "000001", "name": "平安", "quantity": 100, "cost_price": 10}],
        holding_actions=[{"code": "000001", "action": "持有", "note": "观望"}],
        capital_snapshot={"total_capital": 10000, "available_cash": 0},
    )
    assert ctx["market"] == "CN"
    assert ctx["holdings"][0]["code"] == "000001"
