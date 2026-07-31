"""Market data fetcher for the stock-analysis toolkit.

Normalises A-share (AkShare sina source), US (yfinance) and HK data into a
single frame: lowercase ``open/high/low/close/volume`` with a DatetimeIndex.

Network notes
-------------
* A-share uses AkShare's sina backend (``stock_zh_a_daily``) which is more
  resilient than the eastmoney one. If a proxy is set in the environment it
  tends to break domestic endpoints, so this module clears proxy env vars for
  A-share / HK requests and restores them for US requests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..utils import get_logger

log = get_logger("DataFetcher")


@dataclass
class MarketInfo:
    code: str        # raw symbol supplied by user, e.g. "600000" / "AAPL" / "00700"
    market: str      # 'CN' | 'US' | 'HK'
    symbol: str      # provider-specific symbol, e.g. "sh600000" / "AAPL" / "00700"
    name: str = ""


def detect_market(code: str) -> MarketInfo:
    code = code.strip().upper()
    # US: alphabetic tickers
    if code.isalpha():
        return MarketInfo(code, "US", code)
    # HK: 4-5 digit numeric
    if code.isdigit() and len(code) <= 5:
        return MarketInfo(code, "HK", code.zfill(5))
    # A-share: 6 digit
    if code.isdigit() and len(code) == 6:
        prefix = code[0]
        if prefix == "6":
            sym = "sh" + code
        elif prefix in ("0", "3"):
            sym = "sz" + code
        elif prefix in ("8", "4"):
            sym = "bj" + code
        else:
            sym = "sh" + code
        return MarketInfo(code, "CN", sym)
    # already prefixed (sh600000)
    if code.startswith(("SH", "SZ", "BJ")):
        return MarketInfo(code, "CN", code.lower())
    return MarketInfo(code, "CN", code.lower())


_ORIG_PROXY = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}


def _clear_proxy():
    """Clear proxy — domestic (CN/HK) endpoints need direct access."""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)


def _restore_proxy():
    """Restore original proxy — US endpoints (yahoo) need a proxy on CN networks."""
    for k, v in _ORIG_PROXY.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def fetch_kline(info: MarketInfo, days: int = 250) -> pd.DataFrame:
    """Fetch `days` of daily OHLCV, normalised to lowercase columns."""
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=days + 60)  # extra for holidays

    if info.market == "CN":
        _clear_proxy()
        import akshare as ak
        if info.code.startswith(("5", "1")):
            # ETF / LOF 场内基金：sina 专用日线接口（stock_zh_a_daily 对基金返回异常）
            df = ak.fund_etf_hist_sina(symbol=info.symbol)
        else:
            df = ak.stock_zh_a_daily(
                symbol=info.symbol, start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"), adjust="qfq",
            )
        df = df.rename(columns={"date": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()

    elif info.market == "US":
        _restore_proxy()
        import yfinance as yf
        df = yf.download(info.symbol, start=start, end=end + pd.Timedelta(days=1),
                         auto_adjust=True, progress=False)
        # yfinance returns MultiIndex columns when single ticker — flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower).sort_index()

    else:  # HK
        _clear_proxy()
        import akshare as ak
        df = ak.stock_hk_daily(symbol=info.symbol, adjust="qfq")
        df = df.rename(columns={"date": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()

    # keep last N rows, ensure required columns
    df = df.tail(days)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = float("nan")
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_name(info: MarketInfo) -> str:
    """Best-effort name lookup (A-share only; US/HK return the code)."""
    if info.market != "CN":
        return info.code
    try:
        _clear_proxy()
        import akshare as ak
        info_df = ak.stock_info_a_code_name()
        row = info_df[info_df["code"] == info.code]
        if not row.empty:
            return str(row.iloc[0]["name"])
    except Exception:  # noqa: BLE001
        pass
    return info.code


def fetch_fund_flow(info: MarketInfo) -> Optional[pd.DataFrame]:
    """A-share individual fund flow (main force net in/out). May fail."""
    if info.market != "CN":
        return None
    try:
        _clear_proxy()
        import akshare as ak
        market = "sh" if info.symbol.startswith("sh") else "sz"
        if info.symbol.startswith("bj"):
            market = "bj"
        df = ak.stock_individual_fund_flow(stock=info.code, market=market)
        return df.tail(10) if df is not None and not df.empty else None
    except Exception as e:  # noqa: BLE001
        log.debug("fund flow unavailable for %s: %s", info.code, e)
        return None


def fetch_valuation(info: MarketInfo) -> Optional[pd.DataFrame]:
    """A-share valuation indicators (PE/PB/ROE etc.). May fail."""
    if info.market != "CN":
        return None
    try:
        _clear_proxy()
        import akshare as ak
        df = ak.stock_a_indicator_lg(symbol=info.code)
        return df.tail(5) if df is not None and not df.empty else None
    except Exception as e:  # noqa: BLE001
        log.debug("valuation unavailable for %s: %s", info.code, e)
        return None
