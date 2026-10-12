"""Trading-calendar helpers.

``get_trading_days`` / ``is_trading_day`` stay weekday-only. That approximation
is what backtests in this repo were written against; they do not know about
exchange holidays.

A-share scheduling (the 15:05 closing report) needs the real session calendar.
There is no exchange-calendar library in the dependencies, and the weekday
helpers above intentionally ignore holidays. ``is_a_share_trading_day`` therefore
keeps an offline table of Shanghai Stock Exchange weekday closures:

* weekends are always closed, including 调休上班 Saturdays/Sundays (the exchange
  does not open on those makeup office days);
* weekday closures are the dates in the SSE holiday notices. 2026 follows
  上证公告〔2025〕45号 (2025-12-22). 2024–2025 are the same weekday closures
  published for those years (the set used by ``exchange_calendars`` XSHG).

Years outside the table still skip weekends. A holiday in an unpublished year
cannot be known offline, so the function logs one warning and treats remaining
weekdays as sessions until the table is extended.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger("quant_trading_system.calendar")

try:
    from zoneinfo import ZoneInfo

    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING_TZ = timezone(timedelta(hours=8))

# 上交所休市的工作日（周末由星期几判断，不重复列入）。
# 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
_CN_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(s)
    for s in (
        # 2024
        "2024-01-01",
        "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
        "2024-04-04", "2024-04-05",
        "2024-05-01", "2024-05-02", "2024-05-03",
        "2024-06-10",
        "2024-09-16", "2024-09-17",
        "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
        # 2025
        "2025-01-01",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04",
        "2025-04-04",
        "2025-05-01", "2025-05-02", "2025-05-05",
        "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
        # 2026 — 上证公告〔2025〕45号
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    )
)
_CN_HOLIDAY_YEARS: frozenset[int] = frozenset(d.year for d in _CN_HOLIDAYS)
_warned_uncovered_years: set[int] = set()


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


def to_beijing(now: datetime | None = None) -> datetime:
    """Naive clocks are treated as already Beijing; aware clocks are converted."""
    if now is None:
        return datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=BEIJING_TZ)
    return now.astimezone(BEIJING_TZ)


def is_a_share_trading_day(day: str | date | datetime) -> bool:
    """True on an A-share session: weekday, and not an SSE holiday.

    Weekends are closed even when the office calendar marks them as 调休上班.
    """
    d = pd.to_datetime(day).date()
    if d.weekday() >= 5:
        return False
    if d.year not in _CN_HOLIDAY_YEARS and d.year not in _warned_uncovered_years:
        _warned_uncovered_years.add(d.year)
        log.warning(
            "A股休市表只覆盖 %s–%s，%s 年工作日暂按开市处理（周末仍休市）",
            min(_CN_HOLIDAY_YEARS), max(_CN_HOLIDAY_YEARS), d.year,
        )
    return d not in _CN_HOLIDAYS
