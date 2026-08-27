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
        if prefix == "6" or prefix == "5":
            # 沪市股票 6 开头；沪市 ETF/LOF 5 开头（51/56/58）
            sym = "sh" + code
        elif prefix in ("0", "1", "2", "3"):
            # 深市：0/3 股票、1 深市ETF/LOF（159/16x/18x）、2 B股
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
    同时透出 L2 需要的市值/PE/换手率字段（腾讯行情失败时做 L2 回退）。
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
            "总市值": "total_cap", "流通市值": "float_cap",
            "市盈率-动态": "pe", "换手率": "turnover", "市净率": "pb",
        })
        # 注：akshare 升级后 stock_zh_a_spot() 可能不含市值/PE 等列（只有基础行情），
        # 只对存在的列做转换，避免 KeyError 拖垮整个快照。
        for col in ("close", "pct_chg", "volume", "amount",
                    "total_cap", "float_cap", "pe", "turnover", "pb"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0].str.zfill(6)
        keep = ["code", "name", "close", "pct_chg", "volume", "amount"]
        if "total_cap" in df.columns:
            df["total_cap_yi"] = df["total_cap"] / 1e8
            df["float_cap_yi"] = df["float_cap"] / 1e8
            keep += ["total_cap_yi", "float_cap_yi", "pe", "turnover", "pb"]
        return df[[c for c in keep if c in df.columns]]
    except Exception as e:  # noqa: BLE001
        log.warning("新浪全市场快照获取失败: %s", e)
        return None


def fetch_tencent_quotes(codes: list[str], batch: int = 50) -> Optional[pd.DataFrame]:
    """腾讯批量行情（qt.gtimg.cn）→ 现价/涨跌幅/市值/PE/换手率等，约 50 只/请求。

    支持三市场（CN=sh/sz 前缀、HK=hk 前缀、US=us 前缀）。
    返回列：code/name/close/pct_chg/amount_wan/turnover/pe/float_cap_yi/
           total_cap_yi/pb。
    """
    if not codes:
        return None
    rows: list[dict] = []
    for chunk in _batches(codes, batch):
        syms = [_tencent_symbol(c) for c in chunk]
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
                    "code": _norm_code(f[2]),
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


def _tencent_symbol(code: str) -> str:
    """腾讯行情 symbol：CN=sh/sz/bj 前缀、HK=hk+5位、US=us+大写。"""
    info = detect_market(code)
    if info.market == "HK":
        return "hk" + info.code.zfill(5)
    if info.market == "US":
        return "us" + info.code
    return info.symbol  # CN: sh/sz/bj 前缀


def _norm_code(raw: str) -> str:
    """腾讯行情返回的代码标准化：美股去后缀保持大写原样；港股5位原样；A股6位。"""
    s = str(raw).strip().upper()
    if "." in s:  # 美股带交易所后缀（AAPL.OQ / MSFT.NQ）
        s = s.split(".")[0]
    if s.isalpha():  # 美股
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return s
    if len(digits) == 5:  # 港股
        return digits
    return digits.zfill(6)  # A股
def fetch_kline_sina_api(info: MarketInfo, days: int = 120) -> pd.DataFrame:
    """新浪日K JSON 接口（纯 urllib，线程安全，带退避重试）。

    腾讯 fqkline 接口短时间大量请求会被 WAF 临时封禁（HTTP 501），
    新浪接口稳定且无 akshare 的线程安全问题，故漏斗 L3 用它。
    返回：open/high/low/close/volume + DatetimeIndex。
    """
    try:
        _clear_proxy()
        import json
        import time
        import urllib.request
        url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_="
               "/CN_MarketDataService.getKLineData"
               f"?symbol={info.symbol}&scale=240&ma=no&datalen={days}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = None
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(0.3 * (attempt + 1))
        start, end = raw.find("("), raw.rfind(")")
        if start < 0 or end <= start:
            return pd.DataFrame()
        data = json.loads(raw[start + 1:end])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data).rename(columns={"day": "datetime"})
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:  # noqa: BLE001
        log.warning("新浪日K获取失败 %s: %s", info.code, e)
        return pd.DataFrame()


def fetch_growth_factors(info: MarketInfo) -> Optional[dict]:
    """成长因子（Growth）：营收/净利同比增速。A股专用，失败返回 None。

    用新浪财务指标接口（ak.stock_financial_analysis_indicator），与现有新浪
    数据策略一致。仅在单票详情路径调用（批量扫描不取财务，akshare 非线程安全）。
    Returns:
        {"rev_yoy": ..., "profit_yoy": ...}（百分比数值），失败 None。
    """
    if info.market != "CN":
        return None
    try:
        _clear_proxy()
        import akshare as ak

        df = ak.stock_financial_analysis_indicator(symbol=info.code)
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        rev = None
        profit = None
        for c in df.columns:
            if "收入增长" in c or "营业总收入同比增长" in c:
                rev = _safe_float(row[c])
            elif "净利润增长" in c:
                profit = _safe_float(row[c])
        out = {}
        if rev is not None:
            out["rev_yoy"] = rev
        if profit is not None:
            out["profit_yoy"] = profit
        return out or None
    except Exception as e:  # noqa: BLE001
        log.debug("成长因子不可用 %s: %s", info.code, e)
        return None


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_kline_hk_tencent(info: MarketInfo, days: int = 120) -> pd.DataFrame:
    """港股日K（腾讯 hkfqkline 接口，纯 urllib，线程安全，带退避重试）。

    akshare 的 stock_hk_daily 内部用 mini_racer JS 引擎，批量并发会崩
    （libmini_racer address_pool_manager Check failed）；腾讯接口纯 HTTP 无此问题。
    返回：open/high/low/close/volume + DatetimeIndex（前复权）。
    """
    try:
        _clear_proxy()
        import json
        import time
        import urllib.request
        sym = info.symbol if info.symbol.startswith("hk") else "hk" + info.symbol
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
               f"?param={sym},day,,,{days},qfq")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = None
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(0.3 * (attempt + 1))
        data = json.loads(raw)
        node = (data.get("data") or {}).get(sym) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            return pd.DataFrame()
        # 腾讯港股行： [date, open, close, high, low, volume, ...]
        df = pd.DataFrame(
            [r[:6] for r in rows],
            columns=["datetime", "open", "close", "high", "low", "volume"],
        )
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:  # noqa: BLE001
        log.warning("腾讯港股日K获取失败 %s: %s", info.code, e)
        return pd.DataFrame()
