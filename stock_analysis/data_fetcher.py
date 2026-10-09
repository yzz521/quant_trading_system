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
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..utils import get_logger
from . import hithink

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
        # A 股优先走同花顺官方源（纯 HTTP、线程安全、服务端前复权），口径与
        # akshare adjust="qfq" 一致。未配置 Key 或请求失败时回退旧源，
        # 行为与改动前完全相同。港股 / 美股分支不受影响。
        cn = hithink.fetch_kline_cn(info, days=days)
        if cn is not None and not cn.empty:
            return cn
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
    """A-share valuation indicators (PE / PB / market cap etc.). May fail.

    取值顺序：
    1. 同花顺官方估值快照（PE_TTM / PE_MRQ / PB / PS / PCF）—— 纯 HTTP、
       线程安全，可安全用于批量扫描；
    2. 腾讯行情补市值 ``total_mv`` —— 同花顺公开能力不含市值，需另取，
       同样是纯 HTTP 且线程安全；
    3. 前两者都不可用时回退 akshare，行为与改动前一致。

    返回至少含 ``pe_ttm`` 列的 DataFrame（下游 0_opportunity.py 按此列取值）。
    """
    if info.market != "CN":
        return None
    frames: list[pd.DataFrame] = []

    hv = hithink.fetch_valuation_cn([info.code])
    if hv is not None and not hv.empty:
        frames.append(hv.tail(1).reset_index(drop=True))

    try:
        tq = fetch_tencent_quotes([info.code])
        if tq is not None and not tq.empty:
            cap = pd.DataFrame({
                "total_mv": pd.to_numeric(tq["total_cap_yi"], errors="coerce") * 1e8,
                "circ_mv": pd.to_numeric(tq["float_cap_yi"], errors="coerce") * 1e8,
            }).reset_index(drop=True)
            if not cap.isna().all().all():
                frames.append(cap)
    except Exception as e:  # noqa: BLE001
        log.debug("market cap unavailable for %s: %s", info.code, e)

    if not frames:
        try:
            _clear_proxy()
            import akshare as ak
            df = ak.stock_a_indicator_lg(symbol=info.code)
            return df.tail(5) if df is not None and not df.empty else None
        except Exception as e:  # noqa: BLE001
            log.debug("valuation unavailable for %s: %s", info.code, e)
            return None

    if len(frames) == 1:
        return frames[0]
    merged = pd.concat(frames, axis=1)
    return merged.loc[:, ~merged.columns.duplicated()]


def _batches(items: list, size: int):
    """Yield successive chunks of ``items`` (used by batch quote fetches)."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_spot_snapshot(min_amount: Optional[float] = None) -> Optional[pd.DataFrame]:
    """A股全市场快照 → 标准化 code/name/close/pct_chg/volume/amount(+市值/PE/换手/PB)。

    源顺序：**东财 push2（首选）→ 新浪 akshare（旧源，回退）**。

    ⚠️ 2026-09-16：新浪 ``vip.stock.finance.sina.com.cn/quotes_service/...``
    对该网络直接返回「拒绝访问」HTML（加 UA/Referer 无效），
    ``ak.stock_zh_a_spot()`` 因此全线 JSONDecodeError（港股 ``stock_hk_spot()``
    同因失效），看板「今日推荐」与顶部市场宽度一起空掉。故改为东财优先。

    Args:
        min_amount: 只用于**初筛**（按成交额降序取前若干页，约 8 次请求）。
            传 None 表示要全市场（市场宽度用，约 60 次请求，带进程内缓存）。

    用途：漏斗 L1 硬过滤 + 顶部市场宽度。
    """
    if min_amount:
        df = fetch_spot_candidates_em(EM_FS_CN, min_amount=min_amount)
        if df is not None and not df.empty:
            return df
    else:
        df = fetch_spot_snapshot_em(EM_FS_CN)
        if df is not None and not df.empty:
            return df
    return _fetch_spot_snapshot_sina()


def _fetch_spot_snapshot_sina() -> Optional[pd.DataFrame]:
    """旧源（新浪，经 akshare）——东财不可用时回退，行为与改动前一致。"""
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


# ---- 东财 push2 全市场快照（纯 urllib，线程安全） -------------------------- #
# 东财列表接口单页硬上限 100 条 → 全市场约 60 页。
# ⚠️ 东财对单 IP 的短时请求量敏感：实测一次性打 60+ 请求（甚至连打三轮）后，
#    `push2.eastmoney.com` 会对本机 IP 直接拒连（curl http=000，而腾讯行情
#    同刻 200），且数分钟内不恢复；`push2delay.eastmoney.com` 仍然可用。
#    因此这里做了三件事：
#      1. 主域名失败自动切备用域名（`_EM_HOSTS`）；
#      2. 需要全市场时加**进程内 TTL 缓存**（同一轮 10 分钟只打一次）；
#      3. 初筛只按成交额降序取前几页（`fetch_spot_candidates_em`），
#         把 60 次请求降到 8 次以内。
# 用 fid=f12（代码）排序拉全量而不是 fid=f3（涨跌幅）：盘中排序持续变化，
# 翻页期间按涨跌幅排序会漏票/重复。
_EM_HOSTS = ("push2.eastmoney.com", "push2delay.eastmoney.com")
_EM_CLIST_PATH = "/api/qt/clist/get"
_EM_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_EM_FIELDS = "f12,f14,f2,f3,f5,f6,f8,f9,f20,f21,f23"
# 沪主板+科创(m:1+t:2/m:1+t:23)、深主板+创业(m:0+t:6/m:0+t:80)、北交所(m:0+t:81+s:2048)
EM_FS_CN = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EM_FS_HK = "m:116"
_EM_PAGE_SIZE = 100        # 东财单页硬上限（pz 传更大也只返回 100）
_EM_MAX_PAGES = 90         # 全量页数上限
_EM_CAND_PAGES = 8         # 初筛页数（成交额降序，800 只，覆盖任何 top_n 需求）
_EM_WORKERS = 4
_EM_TTL_SEC = 600.0        # 全市场快照进程内缓存（与看板 st.cache_data 同一量级）
# (fs, sort) → (时间戳, 行)
_EM_SPOT_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}

_EM_COLMAP = {
    "f12": "code", "f14": "name", "f2": "close", "f3": "pct_chg",
    "f5": "volume", "f6": "amount", "f8": "turnover", "f9": "pe",
    "f20": "total_cap", "f21": "float_cap", "f23": "pb",
}


def _em_clist_page(pn: int, fs: str, sort: str = "f12", host: Optional[str] = None,
                   attempts: int = 2) -> tuple[list[dict], int]:
    """东财 push2 列表单页 → (diff 行, total)。失败抛异常（由调用方决定降级）。"""
    import json
    import time
    import urllib.request

    host = host or _EM_HOSTS[0]
    url = (f"https://{host}{_EM_CLIST_PATH}?pn={pn}&pz={_EM_PAGE_SIZE}&po=1&np=1"
           f"&fltt=2&invt=2&fid={sort}&fs={fs}&fields={_EM_FIELDS}")
    last: Optional[Exception] = None
    for i in range(max(1, attempts)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _EM_UA})
            _clear_proxy()
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
            data = (json.loads(raw) or {}).get("data") or {}
            return list(data.get("diff") or []), int(data.get("total") or 0)
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 < attempts:
                time.sleep(0.4 * (i + 1))
    raise last if last else RuntimeError("东财快照未知错误")


def _em_get_page(pn: int, fs: str, sort: str = "f12") -> tuple[list[dict], int]:
    """按 `_EM_HOSTS` 顺序试各域名，全失败才抛异常。"""
    last: Optional[Exception] = None
    for host in _EM_HOSTS:
        try:
            return _em_clist_page(pn, fs, sort, host=host)
        except Exception as e:  # noqa: BLE001
            last = e
            log.debug("东财 %s 第 %d 页失败，尝试下一个域名: %s", host, pn, e)
    raise last if last else RuntimeError("东财快照无可用域名")


def _em_spot_rows(fs: str, *, sort: str = "f12", max_pages: Optional[int] = None,
                  stop_below: Optional[float] = None,
                  workers: int = _EM_WORKERS) -> list[dict]:
    """拉列表行。

    * ``max_pages=None``：全量（并发 + TTL 缓存），拿不到 ``total`` 的 60% 判整轮失败；
    * ``max_pages=N``：顺序拉 N 页（可用于成交额降序早停），不缓存。
    """
    key = (fs, sort)
    if max_pages is None:
        hit = _EM_SPOT_CACHE.get(key)
        if hit and time.time() - hit[0] < _EM_TTL_SEC:
            return hit[1]

    try:
        first, total = _em_get_page(1, fs, sort)
    except Exception as e:  # noqa: BLE001
        log.warning("东财快照首页失败: %s", e)
        return []
    if not first:
        return []
    rows = list(first)

    if max_pages is not None:
        # 顺序翻页 + 早停：成交额降序时，某页最低成交额已低于阈值 → 后面只会更低
        last_page = first
        for pn in range(2, max_pages + 1):
            if stop_below is not None:
                lo = _em_page_min_amount(last_page)
                if lo is not None and lo < stop_below:
                    break
            try:
                part, _ = _em_get_page(pn, fs, sort)
            except Exception as e:  # noqa: BLE001
                log.debug("东财快照第 %d 页失败: %s", pn, e)
                break
            if not part:
                break
            rows.extend(part)
            last_page = part
        return rows

    pages = min(_EM_MAX_PAGES, max(1, (total + _EM_PAGE_SIZE - 1) // _EM_PAGE_SIZE))
    failed = 0
    if pages > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(_em_get_page, pn, fs, sort): pn for pn in range(2, pages + 1)}
            for f in as_completed(futs):
                try:
                    part, _ = f.result()
                    rows.extend(part)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    log.debug("东财快照第 %s 页失败: %s", futs[f], e)
    if failed:
        log.warning("东财快照 %d/%d 页失败（已重试并切域名）", failed, pages)
    if total and len(rows) < total * 0.6:
        log.warning("东财快照仅取到 %d/%d 行，判定整轮失败", len(rows), total)
        return []
    _EM_SPOT_CACHE[key] = (time.time(), rows)
    return rows


def _em_page_min_amount(rows: list[dict]) -> Optional[float]:
    vals = [pd.to_numeric(r.get("f6"), errors="coerce") for r in rows]
    vals = [float(v) for v in vals if v is not None and v == v]
    return min(vals) if vals else None


def _em_spot_frame(rows: list[dict]) -> Optional[pd.DataFrame]:
    """东财原始行 → 标准快照列（纯解析，不触网，可单测）。"""
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(columns=_EM_COLMAP)
    if "code" not in df.columns or "amount" not in df.columns:
        return None
    for col in ("close", "pct_chg", "volume", "amount",
                "turnover", "pe", "total_cap", "float_cap", "pb"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = float("nan")
    # 停牌/退市行会出现 "-"，to_numeric 后为 NaN，保留（下游自行 dropna）
    df["code"] = df["code"].astype(str).str.extract(r"(\d{5,6})")[0]
    df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"], keep="first")
    if df.empty:
        return None
    df["total_cap_yi"] = df["total_cap"] / 1e8
    df["float_cap_yi"] = df["float_cap"] / 1e8
    keep = ["code", "name", "close", "pct_chg", "volume", "amount",
            "total_cap_yi", "float_cap_yi", "pe", "turnover", "pb"]
    return df[keep].reset_index(drop=True)


def fetch_spot_snapshot_em(fs: str = EM_FS_CN) -> Optional[pd.DataFrame]:
    """东财**全市场**快照（A股 ``EM_FS_CN`` / 港股 ``EM_FS_HK``）。失败返回 None。

    约 60 次请求（A股 5900 只 / 每页 100），带 5 分钟进程内缓存 + 域名轮换，
    供「需要全部标的」的场景（如市场宽度：涨跌家数）。只想选候选票请用
    ``fetch_spot_candidates_em``（8 次请求）。
    """
    try:
        return _em_spot_frame(_em_spot_rows(fs))
    except Exception as e:  # noqa: BLE001
        log.warning("东财全市场快照失败: %s", e)
        return None


def fetch_spot_candidates_em(fs: str = EM_FS_CN, *, min_amount: float = 5e7,
                            max_pages: int = _EM_CAND_PAGES) -> Optional[pd.DataFrame]:
    """东财**成交额降序前 N 页**快照 —— 供漏斗 L1 初筛（只关心成交额达标的候选）。

    按成交额降序翻页，某页最低成交额已低于 ``min_amount`` 即停（默认最多 8 页 =
    成交额最大的 800 只，足够覆盖 top_n ≤ 80 的任何需求）。相比全市场 60 次请求，
    这里通常 1~8 次，避开东财的 IP 频控。
    """
    try:
        rows = _em_spot_rows(fs, sort="f6", max_pages=max(1, max_pages),
                             stop_below=min_amount)
        return _em_spot_frame(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("东财候选快照失败: %s", e)
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


def fetch_live_prices(codes: list[str]) -> dict[str, float]:
    """批量实时价快照 → ``{标准化代码(大写): 现价}``；失败返回 ``{}``。

    **全系统唯一的「现价」入口**。同一封邮件/同一屏页面里，持仓表的现价、
    持仓量化的现价与盈亏% 必须来自同一份快照，否则同一只票会出现两个价格
    （典型症状：持仓量化拿日 K 末根 = 昨收，而盈亏% 用的是当日价）。

    调用方拿不到实时价时自行回退 K 线，不要在各自的分支里再写一遍取价逻辑。
    """
    out: dict[str, float] = {}
    if not codes:
        return out
    try:
        df = fetch_tencent_quotes(list(codes))
    except Exception as e:  # noqa: BLE001
        log.debug("实时价批量获取失败: %s", e)
        return out
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        p = _safe_float(r.get("close"))
        code = str(r.get("code") or "").strip().upper()
        if code and p is not None and p > 0:
            out[code] = p
    return out


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
    # 优先同花顺财务指标：纯 HTTP 且线程安全，不再受 akshare 并发限制
    # （akshare 的 mini_racer 在批量并发下会崩）。失败回退原有新浪口径。
    g = hithink.fetch_growth_cn(info.code)
    if g:
        return g
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
