"""Trading-calendar helpers.

A lightweight wrapper around ``pandas`` business-day logic. For production
real-time trading you would swap this for an exchange-specific calendar
library (e.g. ``exchange_calendars``), but for backtesting and the strategy
research workflow the simple version below is sufficient and dependency-free.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def get_trading_days(start: str | date, end: str | date, market: str = "CN") -> list[date]:
    """Return a list of trading days between ``start`` and ``end`` inclusive.

    Args:
        start: Start date (inclusive).
        end: End date (inclusive).
        market: ``"CN"`` treats weekends as non-trading; ``"US"`` likewise.
            Holiday calendars are intentionally approximated — for real-money
            trading plug in a proper holiday table.
    """
    start = pd.to_datetime(start)
    end = pd.to_datetime(end)
    # pandas CustomBusinessDay gives weekday-only days; holiday lists are
    # intentionally kept simple to avoid a heavy dependency.
    bday = pd.bdate_range(start, end)
    return [d.date() for d in bday]


def is_trading_day(day: str | date, market: str = "CN") -> bool:
    """Return True when ``day`` is a weekday."""
    d = pd.to_datetime(day)
    return d.weekday() < 5


def next_trading_day(day: str | date, market: str = "CN") -> date:
    d = pd.to_datetime(day)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.date()
