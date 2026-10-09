"""数据源自检 —— 一条命令看清各行情源是否可用（排查「快照不可用」这类问题）.

Run::

    python examples/check_data_sources.py

逐个探测主链路依赖的源，失败只打印原因，不抛异常：
  * 腾讯批量实时价（现价唯一入口）
  * 新浪日K（批量扫描 / 漏斗）
  * 新浪日K JSON（市场指数的日K）
  * 同花顺前复权K线（持仓量化，需 API Key）
  * **东财全市场快照（漏斗 L1 初筛 + 市场宽度）**
  * 同花顺估值快照（L2 估值因子）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import hithink  # noqa: E402
from quant_trading_system.stock_analysis.data_fetcher import (  # noqa: E402
    EM_FS_CN,
    detect_market,
    fetch_kline,
    fetch_kline_sina_api,
    fetch_live_prices,
    fetch_spot_candidates_em,
    fetch_valuation,
)

OK, BAD = "✓", "✗"
rows: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        rows.append((OK, name, str(detail)))
    except Exception as e:  # noqa: BLE001
        rows.append((BAD, name, f"{type(e).__name__}: {str(e)[:110]}"))


def _live():
    p = fetch_live_prices(["601398", "600036"])
    if not p:
        raise RuntimeError("返回空")
    return f"601398={p.get('601398')} 600036={p.get('600036')}"


def _kline_sina():
    df = fetch_kline_sina_api(detect_market("601398"), days=5)
    if df is None or df.empty:
        raise RuntimeError("返回空")
    return f"{len(df)} 行，末根 {df.index[-1].date()}"


def _kline_ths():
    if not hithink.is_enabled():
        raise RuntimeError("未配置同花顺 Key（会回退新浪源）")
    df = fetch_kline(detect_market("601398"), days=5)
    if df is None or df.empty:
        raise RuntimeError("返回空")
    return f"{len(df)} 行，末根 {df.index[-1].date()}"


def _spot_candidates():
    df = fetch_spot_candidates_em(EM_FS_CN, min_amount=5e7)
    if df is None or df.empty:
        raise RuntimeError("返回空（东财可能临时限流，等几分钟再试）")
    return f"{len(df)} 只候选取自成交额前列"


def _valuation():
    df = fetch_valuation(detect_market("601398"))
    if df is None or df.empty:
        raise RuntimeError("返回空")
    return f"pe_ttm={df.iloc[-1].get('pe_ttm')}"


check("腾讯批量实时价（现价唯一入口）", _live)
check("新浪日K JSON（指数/兜底）", _kline_sina)
check("同花顺前复权K线（持仓量化）", _kline_ths)
check("东财全市场快照（初筛/宽度）", _spot_candidates)
check("同花顺估值快照（L2 估值）", _valuation)

print("\n== 数据源自检 ==")
for flag, name, detail in rows:
    print(f" {flag} {name:<28} {detail}")
bad = [n for f, n, _ in rows if f == BAD]
print(f"\n{len(rows) - len(bad)}/{len(rows)} 可用" + (f"；失败：{', '.join(bad)}" if bad else ""))
