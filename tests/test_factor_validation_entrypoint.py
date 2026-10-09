"""因子验证入口与样本可审计性测试。

两个回归缺陷：
1. ``examples/validate_factors.py`` 的真实数据路径把 ``fetch_kline`` 调成
   ``fetch_kline(code, market=..., days=...)`` —— 而该函数收的是 ``MarketInfo``
   且没有 ``market`` 关键字。异常被 per-code 的 try/except 吞掉，于是
   「真实数据」路径恒为空、报告永远只能跑合成数据。**这条路径从没跑通过。**
2. ``BacktestTrade`` 不记录标的代码，导致报告里 ``panel.codes`` 恒为空列表 ——
   「用 ≥30 只股票池重跑验证」这条验收标准根本无法被审计。

本文件把这两条钉死。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine
from quant_trading_system.stock_analysis.research import build_factor_panel

ROOT = Path(__file__).resolve().parents[1]
VF = ROOT / "examples" / "validate_factors.py"

_FACTORS = ["confidence", "opportunity_score", "stock_score", "risk_reward_1"]


def _load_validate_factors():
    spec = importlib.util.spec_from_file_location("_qts_validate_factors", VF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def _kline(n=400, seed=5, trend=0.02, vol=0.15, start="2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.015, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": volume, "amount": volume * close,
        "date": pd.date_range(start, periods=n, freq="B"),
    })
    return add_all_indicators(df)


# --------------------------------------------------------------------------- #
# 1) 真实数据路径必须真的能取到数
# --------------------------------------------------------------------------- #
class TestFetchRealSignature:
    def test_fetch_real_calls_fetch_kline_with_marketinfo(self, monkeypatch):
        mod = _load_validate_factors()
        seen: dict = {}

        def fake_fetch(info, days=250):
            seen["info"] = info
            seen["days"] = days
            return pd.DataFrame({"close": np.arange(200.0)})

        monkeypatch.setattr(mod, "fetch_kline", fake_fetch)
        out = mod._fetch_real(["600519"], 900)

        assert "600519" in out, "取数成功却没进样本集"
        # 第一参数必须是 MarketInfo（而不是裸代码字符串）
        assert getattr(seen["info"], "market", None) == "CN"
        assert seen["info"].symbol == "sh600519"
        assert seen["days"] == 900

    def test_fetch_real_rejects_short_history(self, monkeypatch):
        """K 线不足 120 根不算有效样本（指标预热需要）。"""
        mod = _load_validate_factors()
        monkeypatch.setattr(
            mod, "fetch_kline",
            lambda info, days=250: pd.DataFrame({"close": np.arange(50.0)}),
        )
        assert mod._fetch_real(["600519"], 900) == {}

    def test_fetch_real_survives_one_bad_code(self, monkeypatch):
        """单只失败不能拖垮整批 —— 否则一只退市股就让验证跑不起来。"""
        mod = _load_validate_factors()

        def fake_fetch(info, days=250):
            if info.code == "600519":
                raise RuntimeError("接口抽风")
            return pd.DataFrame({"close": np.arange(200.0)})

        monkeypatch.setattr(mod, "fetch_kline", fake_fetch)
        out = mod._fetch_real(["600519", "000001"], 900)
        assert set(out) == {"000001"}

    def test_source_has_no_bogus_market_kwarg(self):
        """源码级守卫：`fetch_kline(code, market=...)` 这个错写法不许回来。

        只看代码行、跳过注释 —— 注释里为说明这个陷阱会原样引用它。
        """
        src = VF.read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "fetch_kline(code, market=" not in code
        assert "fetch_kline(info, days=days)" in code


# --------------------------------------------------------------------------- #
# 2) 样本必须可审计：交易记录要带标的
# --------------------------------------------------------------------------- #
class TestTradesCarrySymbol:
    def _run(self):
        bt = TradingPlanBacktest(engine=OpportunityEngine(regime_score=65), stride=20)
        return bt.run(_kline(), "600519", "贵州茅台")

    def test_trades_record_code_and_name(self):
        res = self._run()
        assert res.trades, "合成行情应至少产生一笔计划"
        assert {t.code for t in res.trades} == {"600519"}
        assert {t.name for t in res.trades} == {"贵州茅台"}

    def test_panel_records_stock_pool(self):
        """报告里的股票池不能是空列表 —— 否则「≥30 只股票池」无法被审计。"""
        res = self._run()
        panel = build_factor_panel(res.trades, factors=_FACTORS)
        assert not panel.empty
        codes = {c for c in panel["code"].unique() if c}
        assert codes == {"600519"}, f"panel.code 丢了标的：{panel['code'].unique()!r}"

    def test_default_code_is_empty_string_not_none(self):
        """不传 code 时保持空串（下游靠 `or ""` 归一，None 会污染 groupby）。"""
        res = TradingPlanBacktest(engine=OpportunityEngine(regime_score=65), stride=20).run(_kline())
        assert all(t.code == "" for t in res.trades)
