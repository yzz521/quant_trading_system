import pandas as pd
from quant_trading_system.data.quality import validate_ohlcv


def test_validate_missing_column():
    df = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1]})
    issues = validate_ohlcv(df, "X")
    assert any("missing" in i for i in issues)


def test_validate_ok():
    df = pd.DataFrame({
        "open": [10, 10.1],
        "high": [10.2, 10.3],
        "low": [9.9, 10.0],
        "close": [10.1, 10.2],
        "volume": [1e6, 1e6],
    })
    assert validate_ohlcv(df, "X") == []
