"""Candlestick pattern detection for the similar_pattern opportunity slot."""
from __future__ import annotations

import pandas as pd
from quant_trading_system.stock_analysis.patterns import (
    detect_patterns,
    pattern_score,
    recent_pattern_names,
)


def _df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def test_empty_score_neutral():
    assert pattern_score(None) == 50.0
    assert pattern_score(pd.DataFrame()) == 50.0
    assert detect_patterns(pd.DataFrame()) == []


def test_hammer():
    df = _df(
        [
            (12.0, 12.2, 11.8, 11.9, 1e6),
            (11.8, 11.9, 11.5, 11.55, 1e6),
            (10.0, 10.25, 9.0, 10.15, 1e6),  # long lower shadow
        ]
    )
    names = {p.name for p in detect_patterns(df)}
    assert "锤子线" in names
    assert pattern_score(df) > 50


def test_bullish_engulfing():
    df = _df(
        [
            (11.0, 11.2, 10.8, 10.9, 1e6),
            (10.8, 10.9, 10.2, 10.3, 1e6),  # bear
            (10.25, 11.1, 10.2, 10.95, 1e6),  # bull engulf
        ]
    )
    names = {p.name for p in detect_patterns(df)}
    assert "看涨吞没" in names


def test_morning_star():
    df = _df(
        [
            (11.2, 11.3, 10.0, 10.1, 1e6),  # long yin
            (10.05, 10.2, 9.85, 9.95, 1e6),  # small
            (10.1, 11.0, 10.05, 10.85, 1e6),  # yang into first body
        ]
    )
    names = {p.name for p in detect_patterns(df)}
    assert "晨星" in names
    assert pattern_score(df) >= 65


def test_bearish_engulfing_lowers_score():
    df = _df(
        [
            (10.0, 10.2, 9.9, 10.1, 1e6),
            (10.2, 11.0, 10.15, 10.9, 1e6),  # bull
            (10.95, 11.0, 10.1, 10.15, 1e6),  # bear engulf
        ]
    )
    names = {p.name for p in detect_patterns(df)}
    assert "看跌吞没" in names
    assert pattern_score(df) < 50


def test_recent_names_is_list():
    df = _df(
        [
            (10.0, 10.2, 9.9, 10.15, 1e6),
            (10.1, 10.3, 10.0, 10.25, 1e6),
            (10.2, 10.4, 10.1, 10.35, 1e6),
        ]
    )
    names = recent_pattern_names(df)
    assert isinstance(names, list)
