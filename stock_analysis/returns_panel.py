"""持仓历史日收益面板 —— 让组合相关性 / VaR 真正有数据可用。

背景
----
``portfolio_risk.assess_portfolio_risk`` 的平均两两相关、协方差矩阵、参数法
VaR/CVaR **全部依赖 ``returns`` 参数**。但生产路径（调度器邮件、持仓看板）
从来没有传过它，于是这些指标在真实使用中恒为空 —— 界面上留着格子，
实际永远显示「—」。这比「不展示」更糟：用户以为风险指标已经接上了。

本模块负责取这段数据：对一组持仓代码拉近 N 个交易日的日K，算日收益率，
拼成 ``DataFrame(index=交易日, columns=代码)``，并落盘缓存按日合并，
避免每次推送都把全部持仓的行情接口重打一遍。

设计约束
--------
* 缓存按「代码 → {日期: 收益}」存，天然可增量合并；同一日期重复抓取只覆盖。
* 单只标的失败不影响其余 —— 组合风控是附加信息，不能因为它挂掉整封推送。
* 只依赖 numpy/pandas，行情取数复用 ``data_fetcher`` 的既有接口。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

from ..utils import get_logger

log = get_logger("ReturnsPanel")

DEFAULT_DAYS = 120
# 相关性/协方差至少需要这么多有效观测才算得动（与 portfolio_risk._MIN_OBS 一致）
MIN_OBS = 20

Loader = Callable[[str, int], Optional[pd.DataFrame]]


# --------------------------------------------------------------------------- #
# 路径与缓存
# --------------------------------------------------------------------------- #
def default_returns_cache_path() -> Path:
    """缓存路径：``<QTS_DATA_DIR>/results/returns_panel.json``。

    与 ``calibration`` / ``portfolio_risk`` 的 results 口径保持一致
    （``QTS_DATA_DIR`` 指向 ``config/`` 时取同级 ``results/``）。
    """
    import os

    data_dir = os.environ.get("QTS_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
        root = base.parent if base.name == "config" else base
        return root / "results" / "returns_panel.json"
    return Path(__file__).resolve().parents[1] / "results" / "returns_panel.json"


def load_cache(path: Optional[object] = None) -> dict:
    """读取缓存：``{code: {"updated": "YYYY-MM-DD", "series": {date: ret}}}``。"""
    p = Path(path) if path is not None else default_returns_cache_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("收益缓存读取失败（按空处理）: %s", e)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for code, ent in raw.items():
        if not isinstance(ent, dict):
            continue
        series = ent.get("series")
        if not isinstance(series, dict):
            continue
        clean: dict[str, float] = {}
        for d, v in series.items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if np.isfinite(f):
                clean[str(d)] = f
        if clean:
            out[str(code)] = {"updated": str(ent.get("updated") or ""), "series": clean}
    return out


def save_cache(cache: dict, path: Optional[object] = None) -> None:
    """写回缓存（失败只记日志，不影响调用方）。"""
    p = Path(path) if path is not None else default_returns_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("收益缓存写入失败: %s", e)


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #
def _default_loader(code: str, days: int) -> Optional[pd.DataFrame]:
    """按市场选**线程安全**的日K源（与批量扫描同口径）。"""
    from .data_fetcher import (
        detect_market,
        fetch_kline,
        fetch_kline_hk_tencent,
        fetch_kline_sina_api,
    )

    info = detect_market(code)
    if info.market == "CN":
        return fetch_kline_sina_api(info, days=days)
    if info.market == "HK":
        return fetch_kline_hk_tencent(info, days=days)
    return fetch_kline(info, days=days)


def daily_returns(df: Optional[pd.DataFrame]) -> dict[str, float]:
    """从日K算日收益率：``{YYYY-MM-DD: 收益率}``（首日无前收 → 跳过）。"""
    if df is None or len(df) < 2 or "close" not in df.columns:
        return {}
    close = pd.to_numeric(df["close"], errors="coerce")
    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index.strftime("%Y-%m-%d")
    elif "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        dates = pd.Series([str(i) for i in range(len(df))], index=df.index)

    out: dict[str, float] = {}
    prev = None
    for d, c in zip(dates.tolist(), close.tolist()):
        if d is None or (isinstance(d, float) and d != d):
            prev = c
            continue
        if prev is not None and prev > 0 and c is not None and np.isfinite(c):
            r = float(c) / float(prev) - 1.0
            if np.isfinite(r):
                out[str(d)] = r
        prev = c
    return out


def fetch_returns(
    codes: Iterable[str],
    *,
    days: int = DEFAULT_DAYS,
    cache_path: Optional[object] = None,
    loader: Optional[Loader] = None,
    today: Optional[str] = None,
    max_fetch: int = 60,
) -> pd.DataFrame:
    """取一组标的的日收益面板，返回 ``DataFrame(index=日期, columns=代码)``。

    命中当日缓存的标的不再打接口；失败/无数据的标的从结果里剔除（不抛异常）。
    有效观测不足 ``MIN_OBS`` 的列会被丢掉 —— 观测太少的相关系数没有意义，
    留着只会让「平均相关」看起来有值其实不可信。

    Args:
        codes: 标的代码列表。
        days: 每只标的拉取的交易日数。
        cache_path: 缓存文件路径；None 用默认。
        loader: ``(code, days) -> DataFrame``；None 用默认行情源。
        today: 缓存新鲜度判据（``YYYY-MM-DD``）；None 用北京时间的今天。
        max_fetch: 单次最多实际拉取的标的数（防止持仓过多时拖慢推送）。
    """
    want = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not want:
        return pd.DataFrame()

    cache = load_cache(cache_path)
    load = loader or _default_loader
    if today is None:
        from .portfolio_risk import beijing_date

        today = beijing_date()

    fetched = 0
    for code in want:
        ent = cache.get(code) or {}
        if ent.get("updated") == today and ent.get("series"):
            continue                       # 今日已抓过
        if fetched >= max(0, int(max_fetch)):
            log.info("收益面板：本次已达抓取上限 %d，其余用缓存", max_fetch)
            break
        try:
            series = daily_returns(load(code, days))
        except Exception as e:  # noqa: BLE001
            log.debug("取 %s 日收益失败: %s", code, e)
            series = {}
        fetched += 1
        if not series:
            continue
        merged = dict(ent.get("series") or {})
        merged.update(series)
        # 只保留最近 days 个交易日，避免缓存无限膨胀
        keep = sorted(merged)[-max(2, int(days)):]
        cache[code] = {"updated": today, "series": {d: merged[d] for d in keep}}

    if fetched:
        save_cache(cache, cache_path)

    cols = {c: (cache.get(c) or {}).get("series") or {} for c in want}
    cols = {c: s for c, s in cols.items() if len(s) >= MIN_OBS}
    if len(cols) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(cols)
    df = df.sort_index()
    return df


def returns_for_holdings(holdings: Iterable[object], **kw) -> pd.DataFrame:
    """从持仓列表抽代码并取日收益面板（调度器/看板的统一入口）。

    容错到「抽不出代码就返回空表」。**恒返回 DataFrame，绝不返回 None**。

    注意：调用方请勿写 ``returns_for_holdings(h) or None`` —— ``DataFrame`` 的
    布尔值是有歧义的，pandas 会直接抛
    ``ValueError: The truth value of a DataFrame is ambiguous``。
    需要「空表归一成 None」时改用 :func:`returns_or_none`。
    """
    codes: list[str] = []
    for h in holdings or []:
        code = h.get("code") if isinstance(h, dict) else getattr(h, "code", None)
        code = str(code or "").strip()
        if code:
            codes.append(code)
    if not codes:
        return pd.DataFrame()
    return fetch_returns(codes, **kw)


def returns_or_none(panel: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """把「空收益面板」归一成 ``None``，非空原样返回。

    存在的唯一理由：``df or None`` 对 DataFrame 会抛
    ``ValueError: The truth value of a DataFrame is ambiguous``，
    而下游 ``portfolio_risk`` 需要 ``None`` 表示「面板缺失、相关性与 VaR 暂缺」。
    """
    if panel is None:
        return None
    try:
        return None if bool(panel.empty) else panel
    except Exception:  # noqa: BLE001 —— 非 DataFrame 一律当缺失处理
        return None
