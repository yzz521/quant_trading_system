"""全市场初筛器 —— 从整个市场（A股/港股/美股）筛选候选股票。

两层流水线：
  1. 全市场快照 → 按流动性/涨跌幅过滤 → Top N 候选（秒级，不拉K线）
  2. 候选交给 OpportunityBatchScanner 跑机会引擎（见 dashboard / scheduler）

数据源：
  * A股：新浪全市场快照（ak.stock_zh_a_spot，5542 只）
  * 港股：新浪全市场快照（ak.stock_hk_spot）
  * 美股：东财接口在当前网络不可用（push2.eastmoney.com 被代理拦截），
    降级为「配置池 + 知名美股列表」，保证候选可用

过滤规则（默认，可配置）：
  * min_amount：最低成交额（A股 5000 万 / 港股 1000 万 HKD / 美股跳过）
  * max_pct_chg / min_pct_chg：涨跌幅区间（过滤停牌/一字板/暴涨暴跌，默认 -6% ~ 10%）
  * exclude_keywords：名称含这些词剔除（如 ST、退、*）
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..utils import get_logger
from .data_fetcher import fetch_spot_snapshot

log = get_logger("Screener")

# 美股降级候选：知名大盘股（东财不可用时的兜底；可在 notify.yaml us_pool 扩展）
KNOWN_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "NFLX", "AMD",
    "INTC", "BABA", "JD", "PDD", "NIO", "XPEV", "BA", "JPM", "V", "DIS", "KO",
    "PYPL", "UBER", "COIN", "SHOP", "CRM", "ORCL", "ADBE", "CRM", "MU", "QCOM",
]

# 名称中含这些词的剔除（垃圾壳/风险警示）
_EXCLUDE_KEYWORDS = ("ST", "退", "*", "N", "C")


def screen_candidates(
    market: str = "CN",
    top_n: int = 30,
    *,
    min_amount: Optional[float] = None,
    config: Optional[dict] = None,
) -> list[dict]:
    """全市场初筛 → [{code, name}]，按成交额降序取 top_n。

    Args:
        market: CN / HK / US
        top_n: 返回候选数
        min_amount: 最低成交额（覆盖默认值）
        config: 市场级配置（如 {"us_pool": [...]}）
    """
    if market == "US":
        return _screen_us(top_n, config)
    if market == "HK":
        return _screen_hk(top_n, min_amount)
    return _screen_cn(top_n, min_amount)


# --------------------------------------------------------------------------- #
def _screen_cn(top_n: int, min_amount: Optional[float]) -> list[dict]:
    """A股：新浪全市场快照 → 成交额 ≥ 默认 5000 万 → 涨跌幅区间 → Top N。"""
    min_amount = min_amount if min_amount is not None else 5e7
    spot = fetch_spot_snapshot()
    if spot is None or spot.empty:
        log.warning("A股全市场快照不可用，候选为空")
        return []
    return _filter_sort(spot, top_n, min_amount, name_col="name")


def _screen_hk(top_n: int, min_amount: Optional[float]) -> list[dict]:
    """港股：新浪全市场快照（代码/中文名称/最新价/涨跌幅/成交额）。"""
    min_amount = min_amount if min_amount is not None else 1e7
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
        return _filter_sort(df, top_n, min_amount, name_col="name")
    except Exception as e:  # noqa: BLE001
        log.warning("港股全市场快照失败: %s", e)
        return []


def _screen_us(top_n: int, config: Optional[dict]) -> list[dict]:
    """美股：东财接口当前网络不可用 → 配置池 + 知名美股列表（保底）。"""
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
def _filter_sort(
    df: pd.DataFrame,
    top_n: int,
    min_amount: float,
    name_col: str,
) -> list[dict]:
    """通用过滤：成交额下限 + 涨跌幅区间 + 名称关键词剔除，按成交额降序。"""
    if "amount" not in df.columns or "code" not in df.columns:
        return []
    df = df.dropna(subset=["code", "amount"]).copy()
    mask = df["amount"].astype(float) >= min_amount

    if "pct_chg" in df.columns:
        pct = df["pct_chg"].astype(float)
        mask &= pct.between(-6.0, 10.0) | pct.isna()

    if name_col in df.columns:
        import re as _re

        pattern = "|".join(_re.escape(k) for k in _EXCLUDE_KEYWORDS)
        name_ok = ~df[name_col].astype(str).str.contains(
            pattern, case=False, regex=True, na=False
        )
        mask &= name_ok

    df = df[mask].sort_values("amount", ascending=False)
    out: list[dict] = []
    for _, r in df.head(top_n).iterrows():
        out.append({
            "code": str(r["code"]),
            "name": str(r[name_col]) if name_col in df.columns else str(r["code"]),
        })
    return out
