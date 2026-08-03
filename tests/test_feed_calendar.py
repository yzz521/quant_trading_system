import pandas as pd
from quant_trading_system.data.feed import BarFeed

def test_calendar_filters_weekend():
    idx = pd.to_datetime(["2024-01-05", "2024-01-06", "2024-01-08"])
    df = pd.DataFrame({"open":1,"high":1,"low":1,"close":1,"volume":1}, index=idx)
    feed = BarFeed({"X": df}, calendar_market="CN")
    days = [ts.date().isoformat() for ts in feed.timeline]
    assert "2024-01-06" not in days
    assert "2024-01-05" in days
