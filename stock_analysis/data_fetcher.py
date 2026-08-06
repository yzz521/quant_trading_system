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
        elif prefix in ("8", "4", "9"):
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


def _batches(items: list, size: int):
    """Yield successive chunks of ``items`` (used by batch quote fetches)."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_spot_snapshot() -> Optional[pd.DataFrame]:
    """新浪全市场快照（一次调用）→ 标准化 code/name/close/pct_chg/volume/amount。

    东财批量快照在当前网络下不可用，新浪源稳定。用于漏斗 L1 硬过滤。
    """
    try:
        _clear_proxy()
        import akshare as ak
        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "代码": "code", "名称": "name", "最新价": "close",
            "涨跌幅": "pct_chg", "成交量": "volume", "成交额": "amount",
        })
        for col in ("close", "pct_chg", "volume", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0].str.zfill(6)
        return df[["code", "name", "close", "pct_chg", "volume", "amount"]]
    except Exception as e:  # noqa: BLE001
        log.warning("新浪全市场快照获取失败: %s", e)
        return None


def fetch_tencent_quotes(codes: list[str], batch: int = 50) -> Optional[pd.DataFrame]:
    """腾讯批量行情（qt.gtimg.cn）→ 市值/PE/换手率等，约 50 只/请求。

    返回列：code/name/close/pct_chg/amount_wan/turnover/pe/float_cap_yi/
           total_cap_yi/pb。用于漏斗 L2 质量过滤。
    """
    if not codes:
        return None
    rows: list[dict] = []
    for chunk in _batches(codes, batch):
        syms = [detect_market(c).symbol for c in chunk]
        url = "https://qt.gtimg.cn/q=" + ",".join(syms)
        try:
            _clear_proxy()
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
            for line in raw.split(";"):
                line = line.strip()
                if not line.startswith("v_"):
                    continue
                body = line.split("=", 1)[1].strip().strip('"')
                f = body.split("~")
                if len(f) < 47:
                    continue

                def _num(x):
                    try:
                        return float(x)
                    except Exception:  # noqa: BLE001
                        return None

                rows.append({
                    "code": str(f[2]).zfill(6),
                    "name": f[1],
                    "close": _num(f[3]),
                    "pct_chg": _num(f[32]),
                    "amount_wan": _num(f[37]),      # 成交额（万元）
                    "turnover": _num(f[38]),        # 换手率（%）
                    "pe": _num(f[39]),              # 市盈率（动态）
                    "float_cap_yi": _num(f[44]),    # 流通市值（亿元）
                    "total_cap_yi": _num(f[45]),    # 总市值（亿元）
                    "pb": _num(f[46]),              # 市净率
                })
        except Exception as e:  # noqa: BLE001
            log.warning("腾讯批量行情失败（第 %d 批）: %s", rows and len(rows) // batch + 1 or 1, e)
    if not rows:
        return None
    return pd.DataFrame(rows)


def fetch_money_flow(code: str) -> Optional[dict]:
    """新浪个股资金流（最近一日）→ date/net_amount/main_net/turnover/main_ratio。

    main_net 为超大单+大单净流入（元），正数为主力净流入。失败返回 None。
    """
    try:
        _clear_proxy()
        import json
        import urllib.request
        sym = detect_market(code).symbol
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "MoneyFlow.ssl_qsfx_zjlrqs?daima=" + sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        if not data:
            return None
        row = data[0]  # 接口返回按日期倒序（最新在前）

        def _f(x):
            try:
                return float(x)
            except Exception:  # noqa: BLE001
                return None

        return {
            "date": str(row.get("opendate", "")),
            "net_amount": _f(row.get("netamount")),   # 净流入（元）
            "main_net": _f(row.get("r0_net")),        # 主力净流入（元）
            "turnover": _f(row.get("turnover")),
            "main_ratio": _f(row.get("r0_ratio")),
        }
    except Exception as e:  # noqa: BLE001
        log.debug("资金流不可用 %s: %s", code, e)
        return None


def fetch_kline_tencent(info: MarketInfo, days: int = 120) -> pd.DataFrame:
    """腾讯前复权日K（纯 urllib+JSON，线程安全，供漏斗 L3 多线程使用）。

    返回与 ``fetch_kline`` 一致的 DataFrame：open/high/low/close/volume + DatetimeIndex。
    """
    try:
        _clear_proxy()
        import json
        import urllib.request
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
               f"param={info.symbol},day,,,{days},qfq")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        node = (data.get("data") or {}).get(info.symbol) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            return pd.DataFrame()
        rows = [r[:6] for r in rows]  # 部分标的多返回成交额列，只取前6列
        df = pd.DataFrame(
            rows,
            columns=["datetime", "open", "close", "high", "low", "volume"],
        )
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:  # noqa: BLE001
        log.warning("腾讯日K获取失败 %s: %s", info.code, e)
        return pd.DataFrame()


def fetch_stock_news(code: str, days: int = 7, limit: int = 20) -> list[dict]:
    """新浪个股新闻/公告（JSON API）→ [{title, url, ctime}]，按时间倒序，超时/失败返回 []。

    用于漏斗 L4 新闻风险层：标题命中风险关键词（诉讼/减持/立案等）时降分提示。
    """
    try:
        _clear_proxy()
        import json
        import time as _time
        import urllib.request
        sym = detect_market(code).code
        url = ("https://feed.mix.sina.com.cn/api/roll/get?"
               f"pageid=153&lid=2509&k={sym}&num={limit}&page=1")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        items = ((data.get("result") or {}).get("data")) or []
        cutoff = _time.time() - max(int(days), 1) * 86400
        out: list[dict] = []
        for it in items:
            ct = int(it.get("ctime") or 0)
            if ct < cutoff:
                continue
            out.append({
                "title": str(it.get("title") or ""),
                "url": str(it.get("url") or ""),
                "ctime": ct,
            })
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("个股新闻不可用 %s: %s", code, e)
        return []
