"""历史日收益面板测试。

回归缺陷：``portfolio_risk`` 的相关性 / 协方差 / 参数法 VaR/CVaR 全部依赖
``returns``，而生产路径（调度器邮件、持仓看板）从未传过它 —— 这些指标在真实
使用中恒为空，界面上却留着格子。本文件锁死「面板取得出、传得进、算得动」。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.returns_panel import (
    MIN_OBS,
    daily_returns,
    fetch_returns,
    load_cache,
    returns_for_holdings,
    returns_or_none,
    save_cache,
)

_REPO = Path(__file__).resolve().parents[1]


def _kline(prices: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame({"close": prices}, index=idx)


def _loader_from(prices_by_code: dict):
    def _load(code, days):
        p = prices_by_code.get(code)
        if p is None:
            raise RuntimeError("无数据")
        return _kline(p)
    return _load


# --------------------------------------------------------------------------- #
# daily_returns
# --------------------------------------------------------------------------- #
class TestDailyReturns:
    def test_basic_returns_and_first_day_skipped(self):
        out = daily_returns(_kline([100.0, 110.0, 99.0]))
        assert len(out) == 2                       # 首日无前收
        vals = list(out.values())
        assert vals[0] == pytest.approx(0.10)
        assert vals[1] == pytest.approx(-0.10)

    def test_uses_date_column_when_present(self):
        df = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "close": [100.0, 105.0],
        })
        out = daily_returns(df)
        assert list(out) == ["2026-01-02"]
        assert out["2026-01-02"] == pytest.approx(0.05)

    def test_degenerate_inputs(self):
        assert daily_returns(None) == {}
        assert daily_returns(pd.DataFrame({"close": [1.0]})) == {}
        assert daily_returns(pd.DataFrame({"x": [1.0, 2.0]})) == {}

    def test_zero_or_nan_prev_close_is_skipped(self):
        out = daily_returns(_kline([0.0, 10.0, 11.0]))
        assert len(out) == 1                       # 0 → 10 无意义，跳过
        assert list(out.values())[0] == pytest.approx(0.10)


# --------------------------------------------------------------------------- #
# fetch_returns
# --------------------------------------------------------------------------- #
class TestFetchReturns:
    def test_builds_panel_and_drops_short_columns(self, tmp_path):
        long_a = list(np.linspace(100, 120, 40))
        long_b = list(np.linspace(50, 45, 40))
        short = [10.0, 11.0, 12.0]                  # 观测不足 MIN_OBS
        p = tmp_path / "r.json"
        df = fetch_returns(
            ["A", "B", "C"], cache_path=p, today="2026-06-01",
            loader=_loader_from({"A": long_a, "B": long_b, "C": short}),
        )
        assert list(df.columns) == ["A", "B"]
        assert len(df) >= MIN_OBS

    def test_single_code_returns_empty(self, tmp_path):
        """只有一只票时算不出「两两相关」，返回空表比返回半成品更诚实。"""
        p = tmp_path / "r.json"
        df = fetch_returns(["A"], cache_path=p, today="2026-06-01",
                           loader=_loader_from({"A": list(np.linspace(1, 2, 40))}))
        assert df.empty

    def test_loader_failure_does_not_raise(self, tmp_path):
        p = tmp_path / "r.json"
        df = fetch_returns(["A", "B"], cache_path=p, today="2026-06-01",
                           loader=_loader_from({}))
        assert df.empty

    def test_empty_codes(self, tmp_path):
        assert fetch_returns([], cache_path=tmp_path / "r.json").empty

    def test_cache_prevents_refetch_same_day(self, tmp_path):
        p = tmp_path / "r.json"
        calls = {"n": 0}

        def counting(code, days):
            calls["n"] += 1
            return _kline(list(np.linspace(100, 120, 40)))

        fetch_returns(["A", "B"], cache_path=p, today="2026-06-01", loader=counting)
        first = calls["n"]
        assert first == 2
        fetch_returns(["A", "B"], cache_path=p, today="2026-06-01", loader=counting)
        assert calls["n"] == first                 # 同日不再打接口

    def test_new_day_triggers_refetch_and_merges(self, tmp_path):
        p = tmp_path / "r.json"
        fetch_returns(["A", "B"], cache_path=p, today="2026-06-01",
                      loader=_loader_from({"A": [100.0] + list(np.linspace(101, 120, 39)),
                                           "B": [50.0] + list(np.linspace(51, 60, 39))}))
        before = load_cache(p)
        assert before["A"]["updated"] == "2026-06-01"

        fetch_returns(["A", "B"], cache_path=p, today="2026-06-02",
                      loader=_loader_from({"A": [100.0] + list(np.linspace(101, 121, 39)),
                                           "B": [50.0] + list(np.linspace(51, 61, 39))}))
        after = load_cache(p)
        assert after["A"]["updated"] == "2026-06-02"
        assert len(after["A"]["series"]) >= len(before["A"]["series"])

    def test_max_fetch_caps_network_calls(self, tmp_path):
        p = tmp_path / "r.json"
        calls = {"n": 0}

        def counting(code, days):
            calls["n"] += 1
            return _kline(list(np.linspace(100, 120, 40)))

        fetch_returns(["A", "B", "C", "D"], cache_path=p, today="2026-06-01",
                      loader=counting, max_fetch=2)
        assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# 缓存读写
# --------------------------------------------------------------------------- #
class TestCache:
    def test_round_trip(self, tmp_path):
        p = tmp_path / "c.json"
        save_cache({"A": {"updated": "2026-01-01", "series": {"2026-01-02": 0.01}}}, p)
        got = load_cache(p)
        assert got["A"]["series"]["2026-01-02"] == pytest.approx(0.01)

    def test_missing_file_is_empty(self, tmp_path):
        assert load_cache(tmp_path / "nope.json") == {}

    def test_corrupt_file_is_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_cache(p) == {}

    def test_non_numeric_entries_dropped(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text('{"A": {"series": {"d1": 0.1, "d2": "x"}}}', encoding="utf-8")
        assert list(load_cache(p)["A"]["series"]) == ["d1"]

    def test_default_path_follows_qts_data_dir(self, monkeypatch, tmp_path):
        from quant_trading_system.stock_analysis.returns_panel import (
            default_returns_cache_path,
        )

        monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "config"))
        assert default_returns_cache_path() == tmp_path / "results" / "returns_panel.json"
        monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "data"))
        assert default_returns_cache_path() == tmp_path / "data" / "results" / "returns_panel.json"


# --------------------------------------------------------------------------- #
# returns_for_holdings
# --------------------------------------------------------------------------- #
class TestReturnsForHoldings:
    def test_extracts_codes_from_dicts(self, tmp_path, monkeypatch):
        import quant_trading_system.stock_analysis.returns_panel as mod

        seen = {}

        def fake_fetch(codes, **kw):
            seen["codes"] = list(codes)
            return pd.DataFrame()

        monkeypatch.setattr(mod, "fetch_returns", fake_fetch)
        returns_for_holdings([{"code": "600519"}, {"code": "000001"}, {"name": "无代码"}])
        assert seen["codes"] == ["600519", "000001"]

    def test_empty_holdings(self, monkeypatch):
        import quant_trading_system.stock_analysis.returns_panel as mod

        monkeypatch.setattr(mod, "fetch_returns",
                            lambda *a, **kw: pytest.fail("不应取数"))
        assert returns_for_holdings([]).empty
        assert returns_for_holdings(None).empty


# --------------------------------------------------------------------------- #
# 端到端：面板 → 组合风控指标
# --------------------------------------------------------------------------- #
class TestFeedsPortfolioRisk:
    def test_correlation_and_var_become_available(self, tmp_path):
        from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

        rng = np.random.default_rng(3)
        a = list(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
        b = list(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
        returns = fetch_returns(["A", "B"], cache_path=tmp_path / "r.json",
                                today="2026-06-01", loader=_loader_from({"A": a, "B": b}))
        assert not returns.empty

        holdings = [{"code": "A", "name": "A", "quantity": 1000, "current_price": 10},
                    {"code": "B", "name": "B", "quantity": 1000, "current_price": 10}]
        block = portfolio_risk_block(holdings, total_equity=100_000, returns=returns)
        assert block is not None
        assert block["report"]["correlation"]["avg_corr"] is not None
        assert block["report"]["var"]["var"] is not None

    def test_without_returns_metrics_stay_empty(self):
        """对照组：不传 returns 时相关性与 VaR 仍为空（说明修复是必要的）。"""
        from quant_trading_system.stock_analysis.holdings_quant import portfolio_risk_block

        block = portfolio_risk_block(
            [{"code": "A", "quantity": 1000, "current_price": 10},
             {"code": "B", "quantity": 1000, "current_price": 10}],
            total_equity=100_000)
        assert block["report"]["correlation"].get("avg_corr") is None
        assert block["report"]["var"].get("var") is None


# --------------------------------------------------------------------------- #
# 调用方接线
# --------------------------------------------------------------------------- #
class TestCallSitesPassReturns:
    def _src(self, rel: str) -> str:
        return (_REPO / rel).read_text(encoding="utf-8")

    def test_scheduler_passes_returns(self):
        src = self._src("stock_analysis/scheduler.py")
        assert "returns_for_holdings(" in src
        assert "returns=returns" in src

    def test_dashboard_holdings_passes_returns(self):
        src = self._src("dashboard/pages/1_holdings.py")
        assert "returns_for_holdings(" in src
        assert "returns=returns" in src

    @pytest.mark.parametrize("rel", [
        "stock_analysis/scheduler.py",
        "dashboard/pages/1_holdings.py",
    ])
    def test_no_callsite_uses_or_none_idiom(self, rel):
        """回归：``returns_for_holdings(...) or None`` 对 DataFrame 会抛
        ``ValueError: truth value of a DataFrame is ambiguous``，把整段面板
        变成「不可用」，相关性与 VaR 静默缺失。必须走 returns_or_none。

        只看代码行、跳过注释 —— 注释里为了说明陷阱会原样引用这个写法。
        """
        src = self._src(rel)
        code = "\n".join(
            ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
        )
        assert not re.search(r"returns_for_holdings\([^)]*\)\s*or\s+None", code), rel

    def test_returns_or_none_is_wired_in(self):
        for rel in ("stock_analysis/scheduler.py", "dashboard/pages/1_holdings.py"):
            assert "returns_or_none(" in self._src(rel), rel


# --------------------------------------------------------------------------- #
# returns_or_none —— 空面板 → None，非空原样透传
# --------------------------------------------------------------------------- #
class TestReturnsOrNone:
    def test_empty_frame_becomes_none(self):
        assert returns_or_none(pd.DataFrame()) is None

    def test_none_stays_none(self):
        assert returns_or_none(None) is None

    def test_non_empty_frame_passes_through(self):
        df = pd.DataFrame({"A": [0.01, -0.02], "B": [0.0, 0.03]})
        assert returns_or_none(df) is df

    def test_non_frame_input_is_treated_as_missing(self):
        assert returns_or_none("not a frame") is None

    def test_dataframe_truthiness_trap_is_real(self):
        """锁死陷阱本身：证明 ``df or None`` 真的会抛，所以必须用 returns_or_none。"""
        df = pd.DataFrame({"A": [0.01]})
        with pytest.raises(ValueError, match="truth value of a DataFrame is ambiguous"):
            _ = df or None

    def test_returns_for_holdings_output_is_never_none(self, monkeypatch):
        """returns_for_holdings 恒返回 DataFrame —— 调用方不该再靠 or None 兜底。"""
        import quant_trading_system.stock_analysis.returns_panel as mod

        monkeypatch.setattr(mod, "fetch_returns", lambda *a, **kw: pd.DataFrame())
        assert returns_for_holdings([]) is not None
        assert returns_for_holdings([{"code": "600519"}]) is not None
