"""板块轮动 —— Sector Rotation 环节。

架构图中的一环：候选股的所属板块强度影响个股评分（强势板块加分）。

数据源（当前网络实测可用，东财被代理拦截）：
  * 板块强度：同花顺 90 行业 `ak.stock_board_industry_summary_ths()`（0.3s，30 分钟缓存）
  * 成分映射：新浪 49 行业 `ak.stock_sector_spot()` + `ak.stock_sector_detail()`（构建 ~12s，24h 缓存）

失败全部降级：板块强度返回 []、映射返回 {}、因子分返回 50（中性），不阻塞主流程。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import pandas as pd

from ..utils import get_logger

log = get_logger("Sector")

_SECTOR_RANK_TTL = 30 * 60          # 板块强度日内变化，30 分钟刷新
_SECTOR_MAP_TTL = 24 * 3600         # 成分映射静态，24h 缓存

# 模块级缓存 + 锁（akshare 非线程安全，只在主流程调用）
_rank_cache: list[dict] = []
_rank_ts: float = 0.0
_map_cache: dict = {}
_map_ts: float = 0.0
_lock = threading.Lock()


def fetch_sector_rank(market: str = "CN") -> list[dict]:
    """新浪 49 行业板块强度排名（0.1s，30 分钟缓存）。

    Returns:
        [{name, pct_chg, amount, strength}]，strength=0-100
        （0.6×涨跌幅百分位 + 0.4×成交额百分位，跨板块排名分）。
        非 CN 或失败返回 []。
    """
    if market != "CN":
        return []
    global _rank_cache, _rank_ts
    now = time.time()
    with _lock:
        if _rank_cache and now - _rank_ts < _SECTOR_RANK_TTL:
            return list(_rank_cache)
    try:
        import akshare as ak

        # 与 get_stock_sectors 同源（新浪 49 行业），板块名一致可匹配
        df = ak.stock_sector_spot()
        if df is None or df.empty or "板块" not in df.columns:
            return []
        pct_col = next((c for c in ("涨跌幅", "涨跌") if c in df.columns), None)
        amt_col = next((c for c in ("总成交额", "成交额") if c in df.columns), None)
        rows = []
        for _, r in df.iterrows():
            pct = _to_float(r.get(pct_col)) if pct_col else None
            amt = _to_float(r.get(amt_col)) if amt_col else None
            if pct is None and amt is None:
                continue
            rows.append({"name": str(r["板块"]), "pct_chg": pct, "amount": amt})
        if not rows:
            return []
        # 百分位强度分
        pcts = pd.Series([x["pct_chg"] or 0 for x in rows])
        amts = pd.Series([x["amount"] or 0 for x in rows])
        for i, x in enumerate(rows):
            p_rank = pcts.rank(pct=True).iloc[i] * 100 if pcts.nunique() > 1 else 50.0
            a_rank = amts.rank(pct=True).iloc[i] * 100 if amts.nunique() > 1 else 50.0
            x["strength"] = round(0.6 * p_rank + 0.4 * a_rank, 1)
        rows.sort(key=lambda x: x["strength"], reverse=True)
        with _lock:
            _rank_cache = rows
            _rank_ts = now
        return list(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("板块强度获取失败: %s", e)
        return []


def get_stock_sectors(codes: Optional[list] = None) -> dict:
    """新浪 49 行业 → 全市场成分映射 {code6: 行业名}（构建 ~15-30s，24h 缓存）。

    用 stock_sector_spot(indicator="新浪行业") 拿 label+板块名，再逐板块
    stock_sector_detail(sector=label) 拉成分（main-v2 同款方案）。
    一次构建全市场映射（O(1) 查表），scheduler/dashboard/评分多端复用。
    Args:
        codes: 仅用于触发缓存构建的提示（实际构建全量）；可为 None。
    Returns:
        {code: sector_name}；失败返回 {}。
    """
    import re

    global _map_cache, _map_ts
    now = time.time()
    with _lock:
        if _map_cache and now - _map_ts < _SECTOR_MAP_TTL:
            return dict(_map_cache)
    try:
        import akshare as ak

        df = ak.stock_sector_spot(indicator="新浪行业")
        if df is None or df.empty or not {"label", "板块"}.issubset(df.columns):
            log.warning("新浪行业板块列表不可用")
            return {}
        mapping: dict = {}
        for _, row in df.iterrows():
            label, name = str(row["label"]), str(row["板块"]).strip()
            try:
                det = ak.stock_sector_detail(sector=label)
            except Exception:  # noqa: BLE001
                continue
            if det is None or det.empty:
                continue
            code_col = next((c for c in ("code", "代码") if c in det.columns), None)
            if code_col is None:
                continue
            for v in det[code_col].astype(str):
                m = re.search(r"(\d{6})", v)
                if m:
                    mapping[m.group(1)] = name
        with _lock:
            _map_cache = mapping
            _map_ts = now
        log.info("板块成分映射构建完成: %d 只 → %d 个板块", len(mapping), len(df))
        return dict(mapping)
    except Exception as e:  # noqa: BLE001
        log.warning("板块成分映射构建失败: %s", e)
        return {}


def sector_factor(stock_sector: Optional[str], sector_rank: Optional[list]) -> float:
    """板块强度因子：命中返回板块 strength（0-100），未命中/异常返回 50（中性）。"""
    try:
        if not stock_sector or not sector_rank:
            return 50.0
        for s in sector_rank:
            if s.get("name") == stock_sector:
                return float(s.get("strength") or 50.0)
        return 50.0
    except Exception:  # noqa: BLE001
        return 50.0


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
