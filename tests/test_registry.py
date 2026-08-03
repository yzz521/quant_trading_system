from quant_trading_system.strategy import create_strategy, list_strategies, register_strategy
from quant_trading_system.strategy.base import Strategy


def test_list_and_create_ma():
    assert "ma_cross" in list_strategies()
    s = create_strategy("ma_cross", symbols=["DEMO"], fast=5, slow=20)
    assert s.fast == 5
    assert s.slow == 20


def test_unknown_raises():
    try:
        create_strategy("no_such_strat", symbols=["X"])
        assert False
    except KeyError:
        pass


def test_register_custom():
    class Dummy(Strategy):
        def on_bar(self, bar):
            pass

    register_strategy("dummy_test", Dummy, overwrite=True)
    s = create_strategy("dummy_test", symbols=["A"])
    assert isinstance(s, Dummy)
