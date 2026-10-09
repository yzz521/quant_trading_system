"""组合级风控单元测试（纯离线、确定性）。

覆盖：持仓归一化、集中度、行业暴露、相关性与组合波动、参数化/历史 VaR 与 CVaR、
回撤熔断，以及汇总评估的越界识别与建议。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.portfolio_risk import (
    MIN_EQUITY_POINTS,
    RiskLimits,
    apply_brake_to_plans,
    assess_portfolio_risk,
    average_pairwise_correlation,
    concentration,
    correlation_matrix,
    cov_from_returns,
    default_equity_history_path,
    drawdown_brake,
    drawdown_series,
    equity_curve_status,
    equity_values,
    historical_var_cvar,
    holdings_frame,
    industry_exposure,
    load_equity_history,
    max_drawdown,
    norm_ppf,
    parametric_var_cvar,
    portfolio_return_series,
    portfolio_volatility,
    record_equity,
    scale_new_positions,
    summarize,
)


# --------------------------------------------------------------------------- #
# 正态分位
# --------------------------------------------------------------------------- #
def test_norm_ppf_matches_known_quantiles():
    assert norm_ppf(0.95) == pytest.approx(1.6449, abs=1e-3)
    assert norm_ppf(0.975) == pytest.approx(1.9600, abs=1e-3)
    assert norm_ppf(0.99) == pytest.approx(2.3263, abs=1e-3)
    assert norm_ppf(0.90) == pytest.approx(1.2816, abs=1e-3)
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_norm_ppf_is_symmetric():
    for p in (0.01, 0.05, 0.2, 0.4):
        assert norm_ppf(p) == pytest.approx(-norm_ppf(1 - p), abs=1e-6)


def test_norm_ppf_rejects_out_of_range():
    for p in (0.0, 1.0, -0.1, 1.2):
        with pytest.raises(ValueError):
            norm_ppf(p)


# --------------------------------------------------------------------------- #
# 持仓归一化
# --------------------------------------------------------------------------- #
def test_holdings_frame_derives_weight_from_market_value():
    df = holdings_frame([{"code": "A", "market_value": 6000},
                         {"code": "B", "market_value": 2000},
                         {"code": "C", "market_value": 2000}])
    assert df["weight"].tolist() == pytest.approx([0.6, 0.2, 0.2])


def test_holdings_frame_prefers_explicit_weight():
    df = holdings_frame([{"code": "A", "weight": 0.7, "market_value": 9999},
                         {"code": "B", "weight": 0.3, "market_value": 1}])
    assert df["weight"].tolist() == pytest.approx([0.7, 0.3])


def test_holdings_frame_computes_market_value_from_quantity_and_price():
    df = holdings_frame([{"code": "A", "quantity": 100, "current_price": 10},
                         {"code": "B", "quantity": 300, "current_price": 10}])
    assert df["market_value"].tolist() == pytest.approx([1000.0, 3000.0])
    assert df["weight"].tolist() == pytest.approx([0.25, 0.75])


def test_holdings_frame_drops_rows_without_weight_info():
    df = holdings_frame([{"code": "A", "weight": 0.5},
                         {"code": "B"},                       # 无任何权重信息
                         {"code": "", "weight": 0.5}])        # 无代码
    assert df["code"].tolist() == ["A"]


def test_holdings_frame_accepts_objects():
    class H:
        def __init__(self, code, weight, industry=None):
            self.code, self.weight, self.industry = code, weight, industry

    df = holdings_frame([H("A", 0.6, "银行"), H("B", 0.4)])
    assert df["industry"].tolist() == ["银行", "未知"]


def test_holdings_frame_empty():
    assert holdings_frame([]).empty


# --------------------------------------------------------------------------- #
# 集中度与行业
# --------------------------------------------------------------------------- #
def test_concentration_metrics():
    c = concentration([0.5, 0.3, 0.2])
    assert c["n"] == 3
    assert c["top1"] == pytest.approx(0.5)
    assert c["top3"] == pytest.approx(1.0)
    assert c["hhi"] == pytest.approx(0.38)
    assert c["effective_n"] == pytest.approx(1 / 0.38, abs=0.01)


def test_concentration_empty():
    c = concentration([])
    assert c["n"] == 0 and c["top1"] is None and c["effective_n"] is None


def test_industry_exposure_flags_over_limit():
    frame = holdings_frame([
        {"code": "A", "weight": 0.20, "industry": "银行"},
        {"code": "B", "weight": 0.20, "industry": "银行"},
        {"code": "C", "weight": 0.60, "industry": "白酒"},
    ])
    ind = industry_exposure(frame, RiskLimits(max_industry_pct=0.35))
    got = dict(zip(ind["industry"], ind["weight"]))
    assert got == pytest.approx({"白酒": 0.6, "银行": 0.4})
    assert ind["over_limit"].all()
    assert ind["industry"].tolist() == ["白酒", "银行"]      # 按权重降序


def test_industry_exposure_empty_frame():
    assert industry_exposure(pd.DataFrame()).empty


# --------------------------------------------------------------------------- #
# 相关性
# --------------------------------------------------------------------------- #
def _returns(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0, 0.01, n)
    return pd.DataFrame({
        "A": base,
        "B": base * 2.0,                                   # 与 A 完全相关
        "C": rng.normal(0.0, 0.01, n),                     # 独立
    })


def test_correlation_matrix_and_average():
    corr = correlation_matrix(_returns())
    assert corr.shape == (3, 3)
    assert corr.loc["A", "B"] == pytest.approx(1.0)
    avg = average_pairwise_correlation(corr)
    # (1 + ~0 + ~0) / 3 ≈ 0.33
    assert avg == pytest.approx(1 / 3, abs=0.08)


def test_average_pairwise_correlation_needs_two_assets():
    assert average_pairwise_correlation(pd.DataFrame()) is None
    assert average_pairwise_correlation(correlation_matrix(
        pd.DataFrame({"A": [0.01] * 30}))) is None


def test_correlation_matrix_drops_thin_columns():
    r = _returns()
    r["D"] = np.nan
    r.loc[r.index[:1], "D"] = 0.01                        # 只有 1 个观测
    corr = correlation_matrix(r)
    assert "D" not in corr.columns


# --------------------------------------------------------------------------- #
# 波动与 VaR
# --------------------------------------------------------------------------- #
def test_portfolio_volatility_scales_with_horizon():
    cov = np.diag([0.0004, 0.0004])
    w = [0.5, 0.5]
    s1 = portfolio_volatility(w, cov, horizon=1)
    s4 = portfolio_volatility(w, cov, horizon=4)
    assert s1 == pytest.approx(np.sqrt(0.0002))
    assert s4 == pytest.approx(s1 * 2.0)


def test_portfolio_volatility_handles_bad_input():
    assert portfolio_volatility([], np.eye(0)) is None
    assert portfolio_volatility([0.5, 0.5], np.eye(3)) is None


def test_parametric_var_cvar_ordering_and_values():
    cov = np.diag([0.0004, 0.0004])
    out = parametric_var_cvar([0.5, 0.5], cov, confidence=0.95)
    sigma = np.sqrt(0.0002)
    assert out["sigma"] == pytest.approx(sigma, abs=1e-6)
    assert out["var"] == pytest.approx(1.6449 * sigma, abs=1e-4)
    assert out["cvar"] > out["var"]                       # 尾部均值必然更差
    assert out["var"] == pytest.approx(0.02326, abs=1e-4)


def test_historical_var_cvar_uses_actual_distribution():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0, 0.02, 2000)
    out = historical_var_cvar(r, confidence=0.95)
    assert out["n"] == 2000
    # 正态 95% 分位 ≈ 1.645σ
    assert out["var"] == pytest.approx(1.645 * 0.02, rel=0.15)
    assert out["cvar"] > out["var"]


def test_historical_var_cvar_insufficient_samples():
    out = historical_var_cvar([0.01, -0.02], confidence=0.95)
    assert out["var"] is None and out["n"] == 2


def test_cov_and_portfolio_series_align_weights():
    r = _returns()
    cov, w = cov_from_returns(r, [0.5, 0.3, 0.2])
    assert cov.shape == (3, 3)
    assert w.sum() == pytest.approx(1.0)
    s = portfolio_return_series(r, [0.5, 0.3, 0.2])
    assert s is not None and len(s) == len(r)


def test_cov_from_returns_insufficient_columns():
    r = _returns()[["A"]]
    assert cov_from_returns(r, [1.0]) == (None, None)


# --------------------------------------------------------------------------- #
# 回撤熔断
# --------------------------------------------------------------------------- #
def test_drawdown_series_and_max_drawdown():
    dd = drawdown_series([100, 110, 99, 120])
    assert dd[-1] == pytest.approx(0.0)                    # 创新高
    assert dd[2] == pytest.approx(99 / 110 - 1, abs=1e-9)
    assert max_drawdown([100, 110, 99, 120]) == pytest.approx(99 / 110 - 1, abs=1e-9)


@pytest.mark.parametrize("last,expected_brake", [
    (106.0, 1.00),     # -3.6%  未触发
    (104.0, 0.75),     # -5.5%
    (98.0, 0.50),      # -10.9%
    (93.0, 0.25),      # -15.5%
    (87.0, 0.00),      # -20.9%
])
def test_drawdown_brake_levels(last, expected_brake):
    out = drawdown_brake([100, 110, last])
    assert out["brake"] == pytest.approx(expected_brake)
    assert out["triggered"] is (expected_brake < 1.0)
    assert out["max_drawdown"] <= 0


def test_drawdown_brake_without_data():
    out = drawdown_brake([])
    assert out["brake"] == 1.0 and out["current_drawdown"] is None
    assert out["triggered"] is False


def test_drawdown_brake_custom_levels():
    lim = RiskLimits(dd_levels=((-0.02, 0.5),))
    out = drawdown_brake([100, 97], limits=lim)
    assert out["brake"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 净值曲线充分度（第七节第 3 项：把「熔断依据偏薄」显式说出来）
# --------------------------------------------------------------------------- #
class TestEquityCurveSufficiency:
    def test_short_curve_is_not_armed_but_brake_still_applies(self):
        """关键：曲线偏薄**不关闭**熔断（关掉更不安全），只是标注出来。"""
        out = drawdown_brake([100, 110, 98])
        assert out["brake"] == pytest.approx(0.50)      # 熔断照旧生效
        assert out["armed"] is False
        assert out["n_points"] == 3
        assert out["required"] == MIN_EQUITY_POINTS
        assert "偏薄" in out["level"] or "需 ≥" in out["level"]

    def test_long_curve_is_armed(self):
        eq = [100 + (i % 7) - 3 for i in range(MIN_EQUITY_POINTS + 5)]
        out = drawdown_brake(eq)
        assert out["armed"] is True
        assert out["n_points"] == len(eq)
        assert "偏薄" not in out["level"]

    def test_empty_curve_has_no_points_and_is_not_armed(self):
        out = drawdown_brake([])
        assert out["n_points"] == 0
        assert out["armed"] is False
        assert out["brake"] == 1.0

    def test_nan_values_do_not_count_as_points(self):
        out = drawdown_brake([100.0, float("nan"), 110.0, float("inf")])
        assert out["n_points"] == 2

    def test_boundary_exactly_required_is_armed(self):
        eq = [100.0] * MIN_EQUITY_POINTS
        out = drawdown_brake(eq)
        assert out["armed"] is True

    def test_status_note_for_empty(self):
        st = equity_curve_status([])
        assert st["n_points"] == 0 and st["armed"] is False
        assert "尚无净值历史" in st["note"]

    def test_status_note_for_thin(self):
        st = equity_curve_status([100, 101, 99])
        assert st["n_points"] == 3 and st["armed"] is False
        assert "偏薄" in st["note"]
        assert "自动累积" in st["note"]

    def test_status_note_for_armed(self):
        st = equity_curve_status([100.0] * MIN_EQUITY_POINTS)
        assert st["armed"] is True
        assert "可靠判断依据" in st["note"]

    def test_status_accepts_records(self):
        recs = [{"date": f"2026-09-{i + 1:02d}", "equity": 100.0 + i}
                for i in range(3)]
        st = equity_curve_status(records=recs)
        assert st["n_points"] == 3

    def test_status_none_is_safe(self):
        st = equity_curve_status(None)
        assert st["n_points"] == 0 and st["armed"] is False

    def test_custom_required_threshold(self):
        st = equity_curve_status([100, 101, 102], required=3)
        assert st["armed"] is True
        assert st["required"] == 3

    def test_assess_surfaces_thin_curve_in_actions(self):
        """评估结论里必须能看到「依据偏薄」，否则沉默会被读成「已检查且正常」。"""
        rep = assess_portfolio_risk(_clean_portfolio(), equity=[100, 108, 112, 100, 88])
        assert rep.drawdown["armed"] is False
        assert any("自动累积" in a for a in rep.actions)
        assert any("停止开新仓" in a for a in rep.actions)     # 熔断动作仍在

    def test_assess_armed_curve_has_no_thin_note(self):
        eq = [100.0 + (i % 5) for i in range(MIN_EQUITY_POINTS + 3)]
        rep = assess_portfolio_risk(_clean_portfolio(), equity=eq)
        assert rep.drawdown["armed"] is True
        assert not any("自动累积" in a for a in rep.actions)

    def test_assess_without_equity_has_no_note(self):
        rep = assess_portfolio_risk(_clean_portfolio())
        assert rep.drawdown["armed"] is False
        assert rep.actions == []
        assert rep.breaches == []

    def test_summarize_mentions_thin_curve(self):
        rep = assess_portfolio_risk(_clean_portfolio(), equity=[100, 101, 102])
        text = summarize(rep)
        assert "自动累积" in text

    def test_holdings_risk_block_carries_sufficiency(self, tmp_path):
        """持仓风控块要带上曲线充分度，供邮件与看板渲染。"""
        from quant_trading_system.stock_analysis.holdings_quant import (
            portfolio_risk_block,
        )
        rows = [{"code": "600519", "name": "贵州茅台", "quantity": 100,
                 "cost_price": 100.0, "current_price": 100.0}]
        block = portfolio_risk_block(rows, total_equity=100_000.0,
                                     equity_curve=[100_000.0, 101_000.0])
        assert block is not None
        assert block["equity_n"] == 2
        assert block["equity_required"] == MIN_EQUITY_POINTS
        assert block["equity_armed"] is False



# --------------------------------------------------------------------------- #
# 汇总评估
# --------------------------------------------------------------------------- #
def _clean_portfolio() -> list[dict]:
    return [{"code": c, "weight": 0.2, "industry": f"行业{i}", "name": f"N{i}"}
            for i, c in enumerate("ABCDE")]


def test_assess_clean_portfolio_is_normal():
    rep = assess_portfolio_risk(_clean_portfolio())
    assert rep.n_holdings == 5
    assert rep.breaches == []
    assert rep.verdict == "正常"
    assert rep.concentration["top3"] == pytest.approx(0.6)   # 恰好等于上限，不算超


def test_assess_flags_single_name_and_industry():
    rep = assess_portfolio_risk([
        {"code": "A", "name": "重仓股", "weight": 0.5, "industry": "白酒"},
        {"code": "B", "name": "B", "weight": 0.25, "industry": "白酒"},
        {"code": "C", "name": "C", "weight": 0.25, "industry": "银行"},
    ])
    assert any("单票超限" in b for b in rep.breaches)
    assert any("行业超限" in b for b in rep.breaches)
    assert any("前三大持仓合计" in b for b in rep.breaches)
    assert rep.verdict.startswith("超限")
    assert any("重仓股" in a for a in rep.actions)


def test_assess_with_returns_computes_var_and_correlation():
    rep = assess_portfolio_risk(_clean_portfolio(), returns=_returns())
    assert rep.var["method"] == "parametric"
    assert rep.var["var"] is not None and rep.var["cvar"] >= rep.var["var"]
    assert "historical" in rep.var
    assert rep.correlation["avg_corr"] is not None
    assert rep.correlation["max_pair"] is not None


def test_assess_aligns_weights_by_code_when_returns_cover_subset():
    """收益面板只覆盖部分持仓时按代码对齐（不能按位置），并在子集内重新归一。"""
    rep = assess_portfolio_risk(_clean_portfolio(), returns=_returns())   # 仅 A/B/C
    assert rep.n_holdings == 5
    assert rep.var["covered_assets"] == 3
    assert rep.correlation["n_assets"] == 3
    # A/B/C 各 20% → 子集内归一后各 1/3
    cov, w = cov_from_returns(_returns(), [0.2] * 5,
                              weight_index=list("ABCDE"))
    assert w == pytest.approx([1 / 3, 1 / 3, 1 / 3])


def test_cov_from_returns_returns_none_when_no_overlap():
    assert cov_from_returns(_returns(), [0.5, 0.5], weight_index=["X", "Y"]) == (None, None)


def test_assess_flags_high_correlation():
    r = _returns()
    r["B"] = r["A"]                                        # A/B/C 全部同源
    r["C"] = r["A"] * 1.1
    rep = assess_portfolio_risk(_clean_portfolio()[:3], returns=r,
                                limits=RiskLimits(max_avg_corr=0.5))
    assert any("高度相关" in b for b in rep.breaches)


def test_assess_flags_var_breach():
    rng = np.random.default_rng(2)
    r = pd.DataFrame({c: rng.normal(0, 0.06, 300) for c in "ABCDE"})   # 高波动
    rep = assess_portfolio_risk(_clean_portfolio(), returns=r,
                                limits=RiskLimits(max_var_pct=0.01))
    assert any("VaR" in b for b in rep.breaches)
    assert any("总仓位" in a for a in rep.actions)


def test_assess_drawdown_brake_blocks_new_positions():
    equity = [100, 108, 112, 100, 88]                     # 回撤 -21%
    rep = assess_portfolio_risk(_clean_portfolio(), equity=equity)
    assert rep.drawdown["brake"] == 0.0
    assert any("回撤熔断" in b for b in rep.breaches)
    assert any("停止开新仓" in a for a in rep.actions)
    assert rep.verdict.startswith("严重")


def test_assess_empty_portfolio():
    rep = assess_portfolio_risk([])
    assert rep.n_holdings == 0
    assert rep.verdict == "无持仓"
    assert summarize(rep) == "组合风控：无持仓"


def test_scale_new_positions():
    assert scale_new_positions(0.20, 0.5) == pytest.approx(0.10)
    assert scale_new_positions(0.20, 0.0) == pytest.approx(0.0)
    assert scale_new_positions(0.20, 1.0) == pytest.approx(0.20)
    assert scale_new_positions(0.20, 2.0) == pytest.approx(0.20)    # 上限夹紧
    assert scale_new_positions(0.20, -1.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 熔断系数作用到交易计划
# --------------------------------------------------------------------------- #
def test_apply_brake_scales_only_new_position_decisions():
    plans = [
        {"decision": "BUY_NOW", "position_percent": 20.0, "risks": []},
        {"decision": "BUY_ON_PULLBACK", "position_percent": 10.0, "risks": []},
        {"decision": "WATCH", "position_percent": 15.0, "risks": []},
        {"decision": "AVOID", "position_percent": 15.0, "risks": []},
        {"decision": "SELL", "position_percent": 15.0, "risks": []},
    ]
    apply_brake_to_plans(plans, 0.5)
    assert plans[0]["position_percent"] == pytest.approx(10.0)
    assert plans[1]["position_percent"] == pytest.approx(5.0)
    # 观察/回避/卖出不涉及新仓位，不该被动
    assert plans[2]["position_percent"] == pytest.approx(15.0)
    assert plans[3]["position_percent"] == pytest.approx(15.0)
    assert plans[4]["position_percent"] == pytest.approx(15.0)
    assert any("回撤熔断" in r for r in plans[0]["risks"])
    assert plans[2]["risks"] == []


def test_apply_brake_noop_when_brake_is_one():
    plans = [{"decision": "BUY_NOW", "position_percent": 20.0, "risks": []}]
    apply_brake_to_plans(plans, 1.0)
    assert plans[0]["position_percent"] == pytest.approx(20.0)
    assert plans[0]["risks"] == []


def test_apply_brake_zero_means_stop_new_positions():
    plans = [{"decision": "BUY_NOW", "position_percent": 20.0, "risks": []}]
    apply_brake_to_plans(plans, 0.0)
    assert plans[0]["position_percent"] == pytest.approx(0.0)
    assert any("0%" in r for r in plans[0]["risks"])


def test_apply_brake_tolerates_bad_plans():
    plans = [
        {"decision": "BUY_NOW", "position_percent": None, "risks": []},
        {"decision": "BUY_NOW", "position_percent": "abc", "risks": []},
        {"decision": "BUY_NOW", "position_percent": 0.0, "risks": []},
        {"decision": "BUY_NOW", "position_percent": 20.0},          # 无 risks 字段
        "not-a-dict",
    ]
    out = apply_brake_to_plans(plans, 0.5)
    assert len(out) == 5
    assert plans[3]["position_percent"] == pytest.approx(10.0)
    assert plans[3]["risks"] and "回撤熔断" in plans[3]["risks"][0]


def test_apply_brake_does_not_duplicate_note():
    plans = [{"decision": "BUY_NOW", "position_percent": 20.0, "risks": []}]
    apply_brake_to_plans(plans, 0.5)
    apply_brake_to_plans(plans, 0.5)
    assert sum("回撤熔断" in r for r in plans[0]["risks"]) == 1


def test_apply_brake_empty_plans():
    assert apply_brake_to_plans([], 0.5) == []
    assert apply_brake_to_plans(None, 0.5) == []


def test_summarize_contains_key_sections():
    txt = summarize(assess_portfolio_risk(_clean_portfolio(),
                                          returns=_returns(),
                                          equity=[100, 110, 99]))
    assert "组合风控" in txt
    assert "行业暴露" in txt
    assert "平均两两相关" in txt
    assert "VaR" in txt
    assert "回撤" in txt


# --------------------------------------------------------------------------- #
# 净值历史（回撤熔断的数据来源）
# --------------------------------------------------------------------------- #
def test_equity_history_roundtrip_and_same_day_overwrite(tmp_path):
    p = tmp_path / "eq.json"
    assert load_equity_history(p) == []

    record_equity(100_000, date="2026-09-22", path=p)
    record_equity(101_000, date="2026-09-23", path=p)
    record_equity(99_500, date="2026-09-23", path=p)      # 同日覆盖
    recs = load_equity_history(p)
    assert [r["date"] for r in recs] == ["2026-09-22", "2026-09-23"]
    assert recs[-1]["equity"] == pytest.approx(99_500)
    assert equity_values(recs) == pytest.approx([100_000, 99_500])


def test_equity_history_rejects_non_positive(tmp_path):
    p = tmp_path / "eq.json"
    record_equity(0, date="2026-09-23", path=p)
    record_equity(-5, date="2026-09-24", path=p)
    record_equity(float("nan"), date="2026-09-25", path=p)
    assert load_equity_history(p) == []


def test_equity_history_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "eq.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_equity_history(p) == []
    p.write_text('{"a": 1}', encoding="utf-8")
    assert load_equity_history(p) == []


def test_equity_history_sorts_by_date(tmp_path):
    p = tmp_path / "eq.json"
    record_equity(103, date="2026-09-25", path=p)
    record_equity(101, date="2026-09-23", path=p)
    record_equity(102, date="2026-09-24", path=p)
    assert [r["date"] for r in load_equity_history(p)] == [
        "2026-09-23", "2026-09-24", "2026-09-25"]


def test_default_equity_history_path_follows_qts_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "config"))
    assert default_equity_history_path() == tmp_path / "results" / "equity_history.json"
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "data"))
    assert default_equity_history_path() == tmp_path / "data" / "results" / "equity_history.json"


def test_equity_history_feeds_drawdown_brake(tmp_path):
    """端到端：记录净值 → 读回 → 熔断生效。"""
    p = tmp_path / "eq.json"
    for i, v in enumerate([100_000, 105_000, 110_000, 96_000]):
        record_equity(v, date=f"2026-09-2{i + 1}", path=p)
    out = drawdown_brake(equity_values(load_equity_history(p)))
    assert out["current_drawdown"] == pytest.approx(96_000 / 110_000 - 1, abs=1e-4)
    assert out["brake"] == pytest.approx(0.50)          # -12.7% 已越 -10% 档，未到 -15% 档
    assert out["triggered"] is True


# --------------------------------------------------------------------------- #
# 净值日期口径（回归：默认日期用了本机时区）
# --------------------------------------------------------------------------- #
def test_beijing_date_is_shanghai_calendar_day():
    """净值日期必须是**北京时间**的日历日，与调度器/实时盯盘的交易日口径一致。

    原实现用 ``datetime.now().date()``（本机时区）。在时区不是 UTC+8 的机器上，
    北京时间凌晨 0~8 点会写进前一天的键，导致同一交易日被写成两个日期、
    净值曲线多一天或少一天，回撤熔断的输入顺序随之错乱。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_trading_system.stock_analysis.portfolio_risk import beijing_date

    assert beijing_date() == datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def test_record_equity_defaults_to_beijing_date(tmp_path):
    from quant_trading_system.stock_analysis.portfolio_risk import (
        beijing_date,
        load_equity_history,
    )

    p = tmp_path / "eq.json"
    recs = record_equity(123_456.0, path=p)
    assert len(recs) == 1
    assert recs[0]["date"] == beijing_date()
    assert load_equity_history(p)[0]["date"] == beijing_date()


def test_record_equity_explicit_date_still_wins(tmp_path):
    p = tmp_path / "eq.json"
    recs = record_equity(100.0, date="2026-01-02", path=p)
    assert recs[0]["date"] == "2026-01-02"
