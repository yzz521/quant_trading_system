import pandas as pd
from quant_trading_system.data import FallbackDataSource, SyntheticDataSource
from quant_trading_system.data.data_source import DataSource, AssetClass


class EmptySource(DataSource):
    name = "empty"
    def get_history(self, symbol, start, end, frequency="1d", adjust="qfq"):
        return pd.DataFrame()


class BoomSource(DataSource):
    name = "boom"
    def get_history(self, symbol, start, end, frequency="1d", adjust="qfq"):
        raise RuntimeError("network down")


def test_fallback_skips_empty_and_errors():
    src = FallbackDataSource([BoomSource(), EmptySource(), SyntheticDataSource(seed=1)])
    df = src.get_history("X", "2023-01-01", "2023-03-01")
    assert not df.empty
    assert src.last_source == "synthetic"
