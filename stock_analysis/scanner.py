"""Market scanner — screen a basket of stocks by technical conditions.

Conditions are plain callables ``(df, ind) -> bool`` where ``df`` is the
OHLCV frame with indicators attached and ``ind`` is the latest-bar Series.
A set of common presets is provided; pass your own callables for custom
screens. Scans run in a thread pool so a few hundred names finish in seconds.

Example::

    scanner = StockScanner()
    hits = scanner.scan(["600000","000001","600036"],
                        ["多头排列", "放量"], limit=10)
    for h in hits:
        print(h.code, h.name, h.close, h.matched)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

from ..utils import get_logger
from .data_fetcher import detect_market, fetch_kline, fetch_name, MarketInfo
from .indicators import add_all_indicators
from .patterns import scan_signals

log = get_logger("Scanner")


# --------------------------------------------------------------------------- #
# Preset conditions
# --------------------------------------------------------------------------- #
def _cond_ma_bull(df: pd.DataFrame, ind: pd.Series) -> bool:
    ma5, ma20, ma60 = ind.get("ma5"), ind.get("ma20"), ind.get("ma60")
    c = ind.get("close")
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (ma5, ma20, ma60, c)):
        return False
    return bool(ma5 > ma20 > ma60 and c > ma20)


def _cond_macd_golden(df: pd.DataFrame, ind: pd.Series) -> bool:
    if len(df) < 2 or "macd_dif" not in df:
        return False
    d0, d1 = df["macd_dif"].iloc[-1], df["macd_dif"].iloc[-2]
    e0, e1 = df["macd_dea"].iloc[-1], df["macd_dea"].iloc[-2]
    if any(np.isnan(x) for x in (d0, d1, e0, e1)):
        return False
    return bool(d1 <= e1 and d0 > e0)


def _cond_breakout(df: pd.DataFrame, ind: pd.Series, n: int = 20) -> bool:
    if len(df) < n + 1:
        return False
    hh = df["close"].iloc[-n - 1:-1].max()
    return bool(df["close"].iloc[-1] > hh)


def _cond_oversold(df: pd.DataFrame, ind: pd.Series) -> bool:
    r = ind.get("rsi12")
    j = ind.get("j")
    if r is None or j is None:
        return False
    return bool((r < 35) or (j < 10))


def _cond_volume_surge(df: pd.DataFrame, ind: pd.Series, ratio: float = 1.8) -> bool:
    if len(df) < 6:
        return False
    avg = df["volume"].iloc[-6:-1].mean()
    if avg <= 0:
        return False
    return bool(df["volume"].iloc[-1] / avg >= ratio)


def _cond_rsi_healthy(df: pd.DataFrame, ind: pd.Series) -> bool:
    r = ind.get("rsi12")
    if r is None or np.isnan(r):
        return False
    return bool(40 <= r <= 60 and ind.get("macd_hist", 0) > 0)


def _cond_boll_lower(df: pd.DataFrame, ind: pd.Series) -> bool:
    c = ind.get("close")
    lo = ind.get("boll_lower")
    if c is None or lo is None or np.isnan(lo):
        return False
    return bool(c <= lo)


PRESETS: dict[str, Callable] = {
    "多头排列": _cond_ma_bull,
    "MACD金叉": _cond_macd_golden,
    "突破新高": _cond_breakout,
    "超卖": _cond_oversold,
    "放量": _cond_volume_surge,
    "RSI健康": _cond_rsi_healthy,
    "触布林下轨": _cond_boll_lower,
}


# --------------------------------------------------------------------------- #
@dataclass
class ScanHit:
    code: str
    name: str
    market: str
    close: float
    change_pct: float
    score: int
    matched: list = field(default_factory=list)
    signals: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


class StockScanner:
    def __init__(self, max_workers: int = 8) -> None:
        self.max_workers = max_workers

    # ------------------------------------------------------------------ #
    def a_share_universe(self, limit: Optional[int] = None) -> list[str]:
        """Return A-share codes (sh/sz main board + ChiNext). limit caps count."""
        try:
            import akshare as ak
            import os
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ.pop(k, None)
            df = ak.stock_info_a_code_name()
            codes = df["code"].tolist()
            if limit:
                codes = codes[:limit]
            return codes
        except Exception as e:  # noqa: BLE001
            log.warning("无法获取A股列表: %s", e)
            return []

    # ------------------------------------------------------------------ #
    def _evaluate(self, code: str, conditions: list[Callable]) -> Optional[ScanHit]:
        try:
            info = detect_market(code)
            df = fetch_kline(info, days=120)
            if df.empty or len(df) < 30:
                return None
            df = add_all_indicators(df)
            ind = df.iloc[-1]
            matched = [name for name, cond in conditions if cond(df, ind)]
            if not matched:
                return None
            name = fetch_name(info)
            c = float(ind["close"])
            chg = float((df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100) if len(df) >= 2 else 0.0
            signals = scan_signals(df)
            score = len(matched) * 15 + sum(5 for s in signals if s.get("type") == "bull")
            return ScanHit(
                code=info.code, name=name, market=info.market,
                close=round(c, 4), change_pct=round(chg, 2),
                score=min(score, 100), matched=matched, signals=signals,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("scan %s failed: %s", code, e)
            return None

    # ------------------------------------------------------------------ #
    def scan(self, codes: Iterable[str], conditions, limit: int = 50) -> list[ScanHit]:
        """Scan ``codes`` against ``conditions`` (names from PRESETS or callables).

        Returns hits sorted by score desc, capped at ``limit``.
        """
        resolved = []
        for c in conditions:
            if isinstance(c, str):
                fn = PRESETS.get(c)
                if fn is None:
                    log.warning("未知条件 %s，跳过", c)
                    continue
                resolved.append((c, fn))
            else:
                resolved.append((getattr(c, "__name__", "custom"), c))

        codes = list(codes)
        log.info("扫描 %d 只标的，条件=%s", len(codes), [n for n, _ in resolved])
        # Serial scan — fetch_kline handles proxy per-market internally
        # (CN/HK clear proxy, US restore proxy), so no global mutation here.
        hits: list[ScanHit] = []
        for i, code in enumerate(codes, 1):
            r = self._evaluate(code, resolved)
            if r is not None:
                hits.append(r)
            if i % 10 == 0:
                log.info("已扫描 %d/%d ...", i, len(codes))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]
