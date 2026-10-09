"""全市场初筛器 —— 从整个市场（A股/港股/美股）筛选候选股票。

两层流水线：
  1. 全市场快照 → 按流动性/质量/涨跌幅过滤 → **行业分层**取样 → Top N 候选
  2. 候选交给 OpportunityBatchScanner 跑机会引擎（见 dashboard / scheduler）

数据源：
  * A股：东财 push2 全市场快照（``fetch_spot_snapshot_em``，约 5900 只，8 并发 2~3 秒）；
    新浪 akshare 源作为回退（2026-09 起新浪 vip 接口对本网络返回「拒绝访问」）
  * 港股：同一东财接口（``EM_FS_HK``，约 1.8 万只含权证），失败回退 akshare
  * 美股：东财接口在当前网络不可用，改用 nasdaq screener API；
    再失败降级为「配置池 + 知名美股列表」，保证候选可用

过滤规则（默认，可配置）：
  * min_amount：最低成交额（A股 5000 万 / 港股 1000 万 HKD / 美股跳过）
  * pct_range：涨跌幅区间（过滤停牌/一字板/暴涨暴跌，默认 -6% ~ 10%）
  * min_float_cap_yi：最低流通市值（亿元，默认 A股 20 / 港股 5），剔除微盘
  * exclude_keywords：名称含这些词剔除（ST / 退 / *），前缀词（N / C）单独判定

2026-09 的两处修正
------------------
1. **候选池不再只按成交额取 Top-N**。原实现直接取成交额最高的 N 只，天然偏向
   当日最热、最拥挤的题材，且可能整池集中在同一个行业。现在按行业分层取样：
   同一行业最多占候选池的 ``per_industry_ratio``（默认 25%），不足时再放宽补齐。
2. **名称剔除的关键词判定修正**。原实现用子串匹配 ``N``/``C``，会把
   ``TCL科技`` 这类含拉丁字母的正常股票一并剔除；现在 ``N``/``C`` 只在**名称前缀**
   位置生效（对应新股上市首日的 N/C 标记）。
3. **字段透传**：候选 dict 现在带上 pe / 市值 / 换手 / 成交额，供下游质量闸门
   直接使用，不必再为每只票单独取一次快照。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..utils import get_logger
from .data_fetcher import _restore_proxy, fetch_spot_snapshot

log = get_logger("Screener")

# 美股降级候选：知名大盘股（东财不可用时的兜底；可在 notify.yaml us_pool 扩展）
KNOWN_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "NFLX", "AMD",
    "INTC", "BABA", "JD", "PDD", "NIO", "XPEV", "BA", "JPM", "V", "DIS", "KO",
    "PYPL", "UBER", "COIN", "SHOP", "CRM", "ORCL", "ADBE", "CRM", "MU", "QCOM",
]

# 名称中含这些词即剔除（子串匹配）
_EXCLUDE_SUBSTRINGS = ("ST", "退", "*")
# 这些词只在名称**前缀**位置剔除（新股上市首日的 N/C 标记）。
# 用子串匹配会把 TCL科技 / CBA 之类正常名字误杀。
_EXCLUDE_PREFIXES = ("N", "C")
# 兼容旧名（历史代码/测试引用）
_EXCLUDE_KEYWORDS = _EXCLUDE_SUBSTRINGS + _EXCLUDE_PREFIXES

# 候选 dict 透传的数值字段（供质量闸门与评分直接使用）
PASSTHROUGH_FIELDS = (
    "close", "pct_chg", "amount", "turnover", "pe",
    "total_cap_yi", "float_cap_yi", "pb", "industry",
)


def screen_candidates(
    market: str = "CN",
    top_n: int = 30,
    *,
    min_amount: Optional[float] = None,
    config: Optional[dict] = None,
    industry_map: Optional[dict] = None,
    min_float_cap_yi: Optional[float] = None,
    pct_range: Optional[tuple[float, float]] = None,
    per_industry_ratio: float = 0.25,
) -> list[dict]:
    """全市场初筛 → 候选列表（含质量字段与行业），按成交额降序、行业分层取样。

    Args:
        market: CN / HK / US
        top_n: 返回候选数
        min_amount: 最低成交额（覆盖默认值）
        config: 市场级配置（如 {"us_pool": [...]}）
        industry_map: ``{code: 行业名}``；传入后启用行业分层取样（CN 建议传
            ``sector.get_stock_sectors()``）。
        min_float_cap_yi: 最低流通市值（亿元）；None 用市场默认。
        pct_range: 涨跌幅允许区间；None 用默认 (-6%, 10%)。
        per_industry_ratio: 单一行业最多占候选池比例（分层取样用）。
    """
    market = str(market or "CN").upper()
    if market == "US":
        return _screen_us(top_n, config)
    if market == "HK":
        return _screen_hk(top_n, min_amount, industry_map=industry_map,
                          min_float_cap_yi=min_float_cap_yi, pct_range=pct_range,
                          per_industry_ratio=per_industry_ratio)
    return _screen_cn(top_n, min_amount, industry_map=industry_map,
                      min_float_cap_yi=min_float_cap_yi, pct_range=pct_range,
                      per_industry_ratio=per_industry_ratio)


# --------------------------------------------------------------------------- #
def _screen_cn(
    top_n: int,
    min_amount: Optional[float],
    *,
    industry_map: Optional[dict] = None,
    min_float_cap_yi: Optional[float] = None,
    pct_range: Optional[tuple[float, float]] = None,
    per_industry_ratio: float = 0.25,
) -> list[dict]:
    """A股：东财全市场快照 → 成交额/涨跌幅/流通市值过滤 → 行业分层 → Top N。"""
    min_amount = min_amount if min_amount is not None else 5e7
    min_float_cap_yi = min_float_cap_yi if min_float_cap_yi is not None else 20.0
    # 传 min_amount → 只按成交额降序取前几页（约 8 次请求），不拉全市场 60 页
    spot = fetch_spot_snapshot(min_amount=min_amount)
    if spot is None or spot.empty:
        log.warning("A股全市场快照不可用，候选为空")
        return []
    return _filter_sort(
        spot, top_n, min_amount, name_col="name",
        industry_map=industry_map, min_float_cap_yi=min_float_cap_yi,
        pct_range=pct_range, per_industry_ratio=per_industry_ratio,
    )


def _screen_hk(
    top_n: int,
    min_amount: Optional[float],
    *,
    industry_map: Optional[dict] = None,
    min_float_cap_yi: Optional[float] = None,
    pct_range: Optional[tuple[float, float]] = None,
    per_industry_ratio: float = 0.25,
) -> list[dict]:
    """港股：东财成交额降序前 N 页 → 新浪 akshare（旧源回退）。

    ⚠️ 2026-09-16：``ak.stock_hk_spot()`` 与 A 股同源（新浪 vip 接口返回
    「拒绝访问」HTML）已失效，故改为东财优先。
    """
    min_amount = min_amount if min_amount is not None else 1e7
    min_float_cap_yi = min_float_cap_yi if min_float_cap_yi is not None else 5.0
    kw = dict(industry_map=industry_map, min_float_cap_yi=min_float_cap_yi,
              pct_range=pct_range, per_industry_ratio=per_industry_ratio)
    df = None
    try:
        from .data_fetcher import EM_FS_HK, fetch_spot_candidates_em

        df = fetch_spot_candidates_em(EM_FS_HK, min_amount=min_amount)
    except Exception as e:  # noqa: BLE001
        log.warning("港股东财快照失败: %s", e)
    if df is not None and not df.empty:
        df = df.copy()
        df["code"] = df["code"].astype(str).str.zfill(5)
        return _filter_sort(df, top_n, min_amount, name_col="name", **kw)

    try:
        import akshare as ak

        df = ak.stock_hk_spot()
        if df is None or df.empty:
            return []
        df = df.rename(columns={
            "代码": "code", "中文名称": "name", "最新价": "close",
            "涨跌幅": "pct_chg", "成交额": "amount",
        })
        df["code"] = df["code"].astype(str).str.zfill(5)
        for col in ("close", "pct_chg", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return _filter_sort(df, top_n, min_amount, name_col="name", **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("港股全市场快照失败: %s", e)
        return []


_US_CAP_MULT = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}


def _parse_us_market_cap(value) -> Optional[float]:
    """把 nasdaq screener 的市值字符串解析成**亿美元**。

    nasdaq 返回形如 ``"$3.4T"`` / ``"$850.0B"`` / ``"$120M"``。原实现只把
    ``$ , B M`` 四个字符删掉，于是有两个错：

      * ``"$3.4T"`` → ``"3.4T"`` → ``NaN`` —— **万亿级大盘股被静默丢掉**，
        最后只能靠 ``KNOWN_US`` 兜底池，用户以为拿到的是全市场结果；
      * ``"$3.4B"`` → ``3.4`` 再 ``×100`` = 340 亿，而 3.4B USD 实际只有 34 亿
        —— **放大 10 倍**，把「市值 ≥ 100 亿美元」的过滤条件实际放松成 10 亿美元。

    现在按后缀换算成美元，再折算成亿美元，单位固定为「亿美元」。
    """
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "").upper()
    if not s or s in ("N/A", "NA", "-", "--"):
        return None
    mult = 1.0
    if s[-1] in _US_CAP_MULT:
        mult = _US_CAP_MULT[s[-1]]
        s = s[:-1]
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:          # NaN / 非正
        return None
    return v * mult / 1e8


def _screen_us(top_n: int, config: Optional[dict]) -> list[dict]:
    """美股：nasdaq screener API 全市场（~7000 只）→ 按市值降序 Top N。

    美股列表未接东财（其美股板块口径与 akshare 美股源未对齐），沿用 nasdaq
    screener API，1.5s 拉全量。失败 → 回退知名池（_us_fallback_pool）。
    """
    try:
        df = _fetch_nasdaq_universe()
        if df is None or df.empty:
            return _us_fallback_pool(top_n, config)
        # lastsale>2 美元 + 市值 ≥ 100 亿美元 + 剔除 ETF/信托/基金
        df = df.dropna(subset=["symbol", "lastsale"])
        df["lastsale"] = pd.to_numeric(df["lastsale"].astype(str).replace(r"[\$,]", "", regex=True), errors="coerce")
        # 市值统一解析为「亿美元」（支持 T/B/M/K 后缀）
        df["mcap_yi_usd"] = df["marketCap"].map(_parse_us_market_cap)
        mask = (df["lastsale"] > 2) & (df["mcap_yi_usd"] >= 100)
        name_ok = ~df["name"].astype(str).str.contains(
            "ETF|ETN|Fund|Trust", case=False, regex=True, na=False
        )
        df = df[mask & name_ok].sort_values("mcap_yi_usd", ascending=False)
        out: list[dict] = []
        for _, r in df.head(top_n).iterrows():
            sym = str(r["symbol"]).strip().upper()
            if sym:
                out.append({
                    "code": sym, "name": sym,
                    "close": _f(r.get("lastsale")),
                    "pct_chg": _f(r.get("netchange")),
                    "total_cap_yi": _f(r.get("mcap_yi_usd")),
                    "industry": str(r.get("industry") or "") or None,
                })
        return out or _us_fallback_pool(top_n, config)
    except Exception as e:  # noqa: BLE001
        log.warning("美股全市场获取失败，回退知名池: %s", e)
        return _us_fallback_pool(top_n, config)


def _fetch_nasdaq_universe() -> Optional[pd.DataFrame]:
    """nasdaq screener API 全市场列表（~7110 只，1.5s）。

    需恢复代理 + UA + 绕过 SSL（nasdaq API 在此网络下校验异常）。
    Returns:
        DataFrame[symbol/name/lastsale/volume/...]；失败 None。
    """
    try:
        import json
        import ssl
        import urllib.request

        _restore_proxy()
        url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=8000&offset=0"
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
        )
        raw = urllib.request.urlopen(req, timeout=20, context=ctx).read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        rows = (((data or {}).get("data") or {}).get("table") or {}).get("rows") or []
        if not rows:
            return None
        return pd.DataFrame(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("nasdaq screener 获取失败: %s", e)
        return None


def _us_fallback_pool(top_n: int, config: Optional[dict]) -> list[dict]:
    """美股兜底：配置池 + 知名美股列表（去重保序）。"""
    pool = list((config or {}).get("us_pool") or [])
    seen, out = set(), []
    for code in [*pool, *KNOWN_US]:
        code = str(code).strip().upper()
        if code and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": code})
        if len(out) >= top_n:
            break
    return out


# --------------------------------------------------------------------------- #
def _name_excluded(name: str) -> bool:
    """名称是否应剔除。

    * 子串命中 ``ST`` / ``退`` / ``*`` → 剔除（风险警示、退市整理）
    * 前缀命中 ``N`` / ``C`` → 剔除（A 股新股上市首日的 N/C 标记）

    两个细节：
      1. ``N``/``C`` **不能**用子串匹配，否则 ``TCL科技`` 会被误杀。
      2. 前缀规则只在名称含中日韩字符时生效 —— 否则港美股的
         ``NIO Inc`` / ``C3.ai`` 这类纯拉丁名称会被一并剔除。
    """
    s = str(name or "").strip()
    if not s:
        return False
    if any(k in s for k in _EXCLUDE_SUBSTRINGS):
        return True
    if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
        return False
    upper = s.upper()
    return any(upper.startswith(p) for p in _EXCLUDE_PREFIXES)


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def _stratified_pick(
    df: pd.DataFrame,
    top_n: int,
    industry_map: Optional[dict],
    per_industry_ratio: float,
) -> pd.DataFrame:
    """行业分层取样：同一行业最多占 ``per_industry_ratio``，不足再放宽补齐。

    ``df`` 需已按成交额降序排好。没有行业信息时退化为纯成交额排序（但会打日志
    提示覆盖率为 0，避免「以为做了分散其实没做」）。
    """
    if df.empty or top_n <= 0:
        return df.head(0)
    if not industry_map:
        return df.head(top_n)

    codes = df["code"].astype(str).tolist()
    inds = [industry_map.get(c) or "未分类" for c in codes]
    known = sum(1 for i in inds if i != "未分类")
    if known == 0:
        log.info("行业映射覆盖率为 0，候选池退回纯成交额排序")
        return df.head(top_n)

    cap = max(2, int(round(top_n * max(0.05, min(1.0, per_industry_ratio)))))
    counts: dict[str, int] = {}
    picked: list[int] = []
    for idx, ind in enumerate(inds):
        if counts.get(ind, 0) >= cap:
            continue
        picked.append(idx)
        counts[ind] = counts.get(ind, 0) + 1
        if len(picked) >= top_n:
            break
    # 行业分散后数量不足 → 按「当前入选数最少优先、其次成交额优先」补齐。
    # 直接按成交额补齐会让单一行业重新垄断候选池，抵消分层取样的意义。
    if len(picked) < top_n:
        chosen = set(picked)
        rest = [i for i in range(len(inds)) if i not in chosen]
        rest.sort(key=lambda i: (counts.get(inds[i], 0), i))
        for idx in rest:
            picked.append(idx)
            counts[inds[idx]] = counts.get(inds[idx], 0) + 1
            if len(picked) >= top_n:
                break
    picked.sort()
    return df.iloc[picked]


def _filter_sort(
    df: pd.DataFrame,
    top_n: int,
    min_amount: float,
    name_col: str,
    *,
    industry_map: Optional[dict] = None,
    min_float_cap_yi: Optional[float] = None,
    pct_range: Optional[tuple[float, float]] = None,
    per_industry_ratio: float = 0.25,
) -> list[dict]:
    """通用过滤：成交额 + 涨跌幅 + 流通市值 + 名称 → 行业分层 → 候选 dict。

    返回的 dict 除 ``code``/``name`` 外，还透传 pe / 市值 / 换手 / 成交额等字段，
    供下游质量闸门与评分直接使用。
    """
    if "amount" not in df.columns or "code" not in df.columns:
        return []
    df = df.dropna(subset=["code", "amount"]).copy()
    df = df[pd.to_numeric(df["amount"], errors="coerce").fillna(0) > 0]  # 停牌/零成交剔除
    mask = df["amount"].astype(float) >= min_amount

    lo_pct, hi_pct = pct_range if pct_range else (-6.0, 10.0)
    if "pct_chg" in df.columns:
        pct = pd.to_numeric(df["pct_chg"], errors="coerce")
        mask &= pct.between(lo_pct, hi_pct) | pct.isna()

    if min_float_cap_yi and "float_cap_yi" in df.columns:
        cap = pd.to_numeric(df["float_cap_yi"], errors="coerce")
        # 市值缺失时不剔除（避免数据源缺列导致候选清空）
        mask &= (cap >= min_float_cap_yi) | cap.isna()

    if name_col in df.columns:
        keep = ~df[name_col].astype(str).map(_name_excluded)
        mask &= keep

    df = df[mask].sort_values("amount", ascending=False)
    df = _stratified_pick(df, top_n, industry_map, per_industry_ratio)

    out: list[dict] = []
    for _, r in df.iterrows():
        code = str(r["code"])
        item: dict = {
            "code": code,
            "name": str(r[name_col]) if name_col in df.columns else code,
        }
        for f in PASSTHROUGH_FIELDS:
            if f in df.columns:
                v = r.get(f)
                if f in ("industry",):
                    item[f] = str(v) if v not in (None, "") and str(v) != "nan" else None
                else:
                    num = _f(v)
                    if num is not None:
                        item[f] = num
        if not item.get("industry") and industry_map:
            item["industry"] = industry_map.get(code)
        out.append(item)
    return out
