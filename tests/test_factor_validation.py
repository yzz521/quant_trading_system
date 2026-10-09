"""因子验证模块单元测试（纯离线、确定性）。

覆盖：秩相关（含并列）、逐期 IC 序列、IC/IR 汇总、分位收益与单调性、滚动前瞻样本外、
分层 IC、从回测记录构造面板，以及判定口径。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.research import (
    average_ranks,
    build_factor_panel,
    ic_series,
    ic_stats,
    quantile_monotonicity,
    quantile_returns,
    spearman_ic,
    stratified_ic,
    summarize,
    validate_factor,
    validate_factors,
    walk_forward_factor,
    walk_forward_summary,
)
from quant_trading_system.stock_analysis.research.factor_validation import (
    _MIN_PERIODS,
    _verdict,
)
from quant_trading_system.stock_analysis.scoring.factor_weights import (
    classify_verdict,
    multiplier_for,
)


# --------------------------------------------------------------------------- #
# 面板构造
# --------------------------------------------------------------------------- #
def _panel(n_dates: int = 12, n_names: int = 10, *, seed: int = 0,
           signal: float = 1.0, noise: float = 1.0, start: str = "2024-01-01",
           thin_dates: tuple[int, ...] = ()) -> pd.DataFrame:
    """每个日期一个横截面：factor = 0..n-1，ret = signal*factor + noise*ε。"""
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp(start)
    for di in range(n_dates):
        d = base + pd.Timedelta(days=di)
        k = 2 if di in thin_dates else n_names
        f = np.arange(k, dtype=float)
        r = signal * f + noise * rng.normal(0.0, 1.0, k)
        for j in range(k):
            rows.append({"date": d, "code": f"C{j:02d}", "factor": f[j], "ret": r[j]})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 秩工具与 IC
# --------------------------------------------------------------------------- #
def test_average_ranks_handles_ties():
    r = average_ranks([10.0, 20.0, 20.0, 30.0])
    assert r.tolist() == pytest.approx([1.0, 2.5, 2.5, 4.0])


def test_spearman_ic_perfect_and_reversed():
    f = [1, 2, 3, 4, 5]
    assert spearman_ic(f, [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman_ic(f, [50, 40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_ic_is_rank_based_not_linear():
    """秩相关只看排序：把收益做非线性单调变换后 IC 不变（皮尔逊会变）。"""
    f = [1, 2, 3, 4, 5]
    linear = [1, 2, 3, 4, 5]
    cubic = [x ** 3 for x in linear]
    assert spearman_ic(f, cubic) == pytest.approx(spearman_ic(f, linear))


def test_spearman_ic_constant_factor_is_nan():
    # 常数因子没有排序信息 → NaN（区别于「测过、无相关」的 0）
    assert np.isnan(spearman_ic([1, 1, 1, 1], [1, 2, 3, 4]))


def test_spearman_ic_respects_min_names():
    assert np.isnan(spearman_ic([1, 2], [1, 2], min_names=3))
    assert np.isfinite(spearman_ic([1, 2, 3], [1, 2, 3], min_names=3))


def test_spearman_ic_ignores_non_finite():
    f = [1, 2, np.nan, 4, 5]
    r = [1, 2, 3, np.nan, 5]
    # 仅剩 (1,1),(2,2),(5,5) 三点，仍完全同序
    assert spearman_ic(f, r) == pytest.approx(1.0)


def test_spearman_ic_length_mismatch_raises():
    with pytest.raises(ValueError):
        spearman_ic([1, 2, 3], [1, 2])


# --------------------------------------------------------------------------- #
# 逐期 IC 与汇总
# --------------------------------------------------------------------------- #
def test_ic_series_one_value_per_date():
    panel = _panel(n_dates=9, signal=2.0, noise=0.5)
    ic = ic_series(panel, "factor", "ret")
    assert len(ic) == 9
    assert ic.index.is_monotonic_increasing
    assert (ic > 0.5).all()          # 强信号 → 每期 IC 都高


def test_ic_series_skips_thin_cross_sections():
    panel = _panel(n_dates=6, n_names=10, signal=2.0, noise=0.5, thin_dates=(2, 4))
    ic = ic_series(panel, "factor", "ret", min_names=5)
    assert len(ic) == 4              # 两个「只有 2 只票」的日期被剔除


def test_ic_series_raises_on_missing_column():
    panel = _panel()
    with pytest.raises(KeyError):
        ic_series(panel, "nope", "ret")


def test_ic_stats_basic_fields():
    ic = pd.Series([0.10, 0.20, 0.30, 0.40, 0.05])
    s = ic_stats(ic)
    assert s["n_periods"] == 5
    assert s["ic_mean"] == pytest.approx(0.21)
    assert s["ic_win_rate"] == pytest.approx(1.0)
    assert s["ir"] is not None and s["ir"] > 0
    assert s["t_stat"] is not None


def test_ic_stats_empty_and_single():
    empty = ic_stats(pd.Series(dtype=float))
    assert empty["n_periods"] == 0 and empty["ic_mean"] is None and empty["ir"] is None
    one = ic_stats(pd.Series([0.3]))
    assert one["n_periods"] == 1 and one["ir"] is None   # 单期无法定义波动


def test_ic_stats_ignores_nan():
    s = ic_stats(pd.Series([0.2, np.nan, 0.4, np.inf]))
    assert s["n_periods"] == 2


# --------------------------------------------------------------------------- #
# 分位收益与单调性
# --------------------------------------------------------------------------- #
def test_quantile_returns_monotone_up():
    panel = _panel(n_dates=10, n_names=20, signal=5.0, noise=0.0)
    q = quantile_returns(panel, "factor", "ret", n_quantiles=5)
    assert q["quantile"].tolist() == [1, 2, 3, 4, 5]
    assert q["mean_return"].is_monotonic_increasing
    m = quantile_monotonicity(q)
    assert m["monotone_up"] is True
    assert m["direction"] == 1
    assert m["top_minus_bottom"] > 0


def test_quantile_returns_monotone_down_for_reversed_factor():
    panel = _panel(n_dates=10, n_names=20, signal=-5.0, noise=0.0)
    q = quantile_returns(panel, "factor", "ret", n_quantiles=5)
    m = quantile_monotonicity(q)
    assert m["monotone_down"] is True
    assert m["direction"] == -1
    assert m["top_minus_bottom"] < 0


def test_quantile_returns_skip_thin_periods():
    panel = _panel(n_dates=6, n_names=20, signal=5.0, noise=0.0, thin_dates=(1, 3))
    q = quantile_returns(panel, "factor", "ret", n_quantiles=5)
    assert (q["n_periods"] == 4).all()


def test_quantile_returns_empty_when_too_thin():
    panel = _panel(n_dates=4, n_names=4, signal=5.0, noise=0.0)
    q = quantile_returns(panel, "factor", "ret", n_quantiles=5)
    assert q.empty
    assert quantile_monotonicity(q)["n_quantiles"] == 0


# --------------------------------------------------------------------------- #
# 分组单调性判定口径（2026-10 裁决）
#
# 原口径要求「严格逐档单调」= 4 个相邻对零逆序。问题：分位均值本身有抽样噪声，
# 零逆序要求会把「整体趋势很强但有一对小噪声」的因子与「完全无信号」的因子
# 一视同仁判死 —— 实测 risk_reward_1 的 ρ=0.7 就因此被判「分组不单调」。
# 新口径：逆序 0 对通过；逆序 1 对且 |ρ|≥0.6 通过；逆序 ≥2 对不通过。
# --------------------------------------------------------------------------- #
def _stats(ic=0.5, ir=2.0, n=20, ic_std=0.25, t_stat=None):
    """``t_stat`` 显式传：``_verdict`` 用它区分「确认无区分力」与「测不出来」。"""
    return {"ic_mean": ic, "ir": ir, "n_periods": n, "ic_std": ic_std,
            "t_stat": t_stat}


def _mono(n_inv, rho, *, up=False, down=False):
    return {"monotone_up": up, "monotone_down": down,
            "n_inversions": n_inv, "spearman": rho,
            "direction": 1 if (rho or 0) > 0 else -1}


def test_verdict_accepts_quasi_monotone_with_strong_rho():
    """准单调：仅 1 对相邻逆序，但 ρ 补偿达标 → 可用。"""
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict

    v = _verdict(_stats(), _mono(1, 0.7))
    assert v.startswith("可用")
    assert "准单调" in v


def test_verdict_rejects_quasi_monotone_with_weak_rho():
    """1 对逆序但 ρ 不够强 → 仍判不单调（放宽了逆序就要在强度上更严）。"""
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict

    v = _verdict(_stats(), _mono(1, 0.3))
    assert "不单调" in v
    assert not v.startswith("可用")


def test_verdict_rejects_two_or_more_inversions():
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict

    assert "不单调" in _verdict(_stats(), _mono(2, 0.9))


def test_verdict_strict_monotone_still_usable():
    """严格单调（0 对逆序）不受新口径影响。"""
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict

    assert _verdict(_stats(), _mono(0, 1.0, up=True)).startswith("可用")


def test_verdict_texts_survive_keyword_classification():
    """文案必须能被 classify_verdict 按关键词正确归类。

    否则会静默变成「未知」→ 乘数 1.0（不降权）—— 一个该降权的因子悄悄全额保留。
    """
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict
    from quant_trading_system.stock_analysis.scoring import classify_verdict

    assert classify_verdict(_verdict(_stats(), _mono(1, 0.2))) == "nonmonotone"
    assert classify_verdict(_verdict(_stats(), _mono(3, 0.9))) == "nonmonotone"
    assert classify_verdict(_verdict(_stats(), _mono(1, 0.8))) == "usable"
    assert classify_verdict(_verdict(_stats(), _mono(0, 1.0, up=True))) == "usable"


def test_quantile_monotonicity_counts_inversions():
    panel = _panel(n_dates=10, n_names=20, signal=5.0, noise=0.0)
    q = quantile_returns(panel, "factor", "ret", n_quantiles=5)
    m = quantile_monotonicity(q)
    assert m["n_inversions"] == 0
    assert m["monotone_up"] is True


# --------------------------------------------------------------------------- #
# 单因子 / 多因子体检
# --------------------------------------------------------------------------- #
def test_validate_factor_verdict_usable():
    panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
    rep = validate_factor(panel, "factor", "ret")
    assert rep["factor"] == "factor"
    assert rep["ic"]["ic_mean"] > 0.8
    assert rep["ic"]["n_periods"] == 12
    assert rep["verdict"].startswith("可用")
    assert rep["n_obs"] == 12 * 20


def test_validate_factor_usable_with_noisy_signal_has_finite_ir():
    """有噪声但方向稳定的因子：IR 必须可计算且显著。"""
    panel = _panel(n_dates=30, n_names=20, signal=2.0, noise=2.0, seed=3)
    rep = validate_factor(panel, "factor", "ret")
    assert rep["ic"]["ir"] is not None and rep["ic"]["ir"] > 1.0
    assert rep["ic"]["t_stat"] > 2.0
    assert rep["verdict"].startswith("可用")


def test_verdict_treats_zero_ic_std_as_stable_not_unstable():
    """IC 标准差为 0（每期完全相同）不是「不稳定」，而是方向一致到极致。"""
    from quant_trading_system.stock_analysis.research.factor_validation import _verdict

    stats = {"n_periods": 10, "ic_mean": 0.9, "ic_std": 0.0, "ir": None,
             "t_stat": None, "ic_win_rate": 1.0}
    mono = {"monotone_up": True, "monotone_down": False}
    assert _verdict(stats, mono).startswith("可用")


def test_validate_factor_verdict_no_signal():
    """IC 恰好为 0 的因子：结论必须是「无区分力」。

    用确定性构造而非随机噪声：n=4 时收益秩置换 [2,4,1,3] 的 Σd²=10，
    Spearman = 1 - 6·10/(4·15) = 0，每期 IC 精确为 0（随机噪声会引入采样波动，
    断言 0.02 这种紧阈值会偶发失败）。
    """
    perm = [2, 4, 1, 3]
    rows = []
    base = pd.Timestamp("2024-01-01")
    for di in range(10):
        d = base + pd.Timedelta(days=di)
        for j in range(4):
            rows.append({"date": d, "code": f"C{j}", "factor": float(j), "ret": float(perm[j])})
    panel = pd.DataFrame(rows)

    rep = validate_factor(panel, "factor", "ret", n_quantiles=2)
    assert rep["ic"]["ic_mean"] == pytest.approx(0.0, abs=1e-12)
    assert rep["ic"]["n_periods"] == 10
    assert "无区分力" in rep["verdict"]


def test_validate_factor_verdict_insufficient_periods():
    panel = _panel(n_dates=4, n_names=20, signal=5.0, noise=0.5)
    rep = validate_factor(panel, "factor", "ret")
    assert rep["ic"]["n_periods"] < _MIN_PERIODS
    assert "样本不足" in rep["verdict"]


def test_validate_factors_sorted_by_abs_ic():
    """面板只有一个收益列，所以「弱因子」必须是**与收益几乎无关**的另一列。"""
    panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5, seed=1).rename(
        columns={"factor": "strong"})
    rng = np.random.default_rng(99)
    # 加 20 倍标准差噪声 → 排序几乎被打乱，IC 远小于强因子
    panel["noisy"] = panel["strong"] + rng.normal(0.0, 20.0, len(panel))
    out = validate_factors(panel, ["noisy", "strong"], return_col="ret")
    assert out["factor"].tolist() == ["strong", "noisy"]
    assert out["ic_mean"].abs().is_monotonic_decreasing
    assert set(out.columns) >= {"ic_mean", "ir", "t_stat", "verdict", "monotone"}


def test_validate_factors_defaults_to_all_factor_columns():
    panel = _panel(n_dates=10, n_names=20, signal=5.0, noise=0.5)
    panel["factor2"] = panel["factor"] * 2
    out = validate_factors(panel, return_col="ret")
    assert set(out["factor"]) == {"factor", "factor2"}   # date/code/ret 被排除


# --------------------------------------------------------------------------- #
# 滚动前瞻
# --------------------------------------------------------------------------- #
def test_walk_forward_never_trains_on_future():
    panel = _panel(n_dates=20, n_names=20, signal=5.0, noise=0.5)
    wf = walk_forward_factor(panel, "factor", "ret", n_splits=5)
    assert len(wf) == 4
    for _, r in wf.iterrows():
        assert pd.Timestamp(r["train_end"]) < pd.Timestamp(r["test_start"])
        assert r["train_periods"] > 0 and r["test_periods"] > 0


def test_walk_forward_sign_kept_for_stable_factor():
    panel = _panel(n_dates=20, n_names=20, signal=5.0, noise=0.5)
    wf = walk_forward_factor(panel, "factor", "ret", n_splits=5)
    assert wf["sign_kept"].all()


def test_walk_forward_embargo_shortens_train_and_keeps_order():
    panel = _panel(n_dates=20, n_names=20, signal=5.0, noise=0.5)
    base = walk_forward_factor(panel, "factor", "ret", n_splits=5, embargo_periods=0)
    emb = walk_forward_factor(panel, "factor", "ret", n_splits=5, embargo_periods=2)
    for (_, b), (_, e) in zip(base.iterrows(), emb.iterrows()):
        assert e["train_periods"] == b["train_periods"] - 2
        assert pd.Timestamp(e["train_end"]) < pd.Timestamp(e["test_start"])
        assert pd.Timestamp(e["train_end"]) <= pd.Timestamp(b["train_end"])


def test_walk_forward_works_without_date_column():
    panel = _panel(n_dates=20, n_names=20, signal=5.0, noise=0.5).drop(columns=["date"])
    wf = walk_forward_factor(panel, "factor", "ret", n_splits=5)
    assert len(wf) == 4
    assert wf["sign_kept"].all()
    assert wf["train_end"].isna().all()


def test_walk_forward_empty_panel():
    wf = walk_forward_factor(pd.DataFrame(), "factor", "ret")
    assert wf.empty


# --------------------------------------------------------------------------- #
# 分层
# --------------------------------------------------------------------------- #
def test_stratified_ic_splits_by_regime():
    panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
    panel["regime"] = np.where(panel["date"] < pd.Timestamp("2024-01-07"), "A", "B")
    out = stratified_ic(panel, "factor", "ret", "regime")
    assert set(out["regime"]) == {"A", "B"}
    assert (out["ic_mean"] > 0.5).all()
    assert out["n_obs"].sum() == len(panel)


def test_stratified_ic_missing_column_returns_empty():
    panel = _panel()
    assert stratified_ic(panel, "factor", "ret", "nope").empty


# --------------------------------------------------------------------------- #
# 面板构造
# --------------------------------------------------------------------------- #
class _Trade:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_build_factor_panel_skips_not_entered():
    trades = [
        _Trade(entry_executed=True, return_pct=3.0, date="2024-01-02", code="A",
               confidence=0.7, opportunity_score=80, stock_score=70, risk_reward_1=2.5),
        _Trade(entry_executed=False, return_pct=0.0, date="2024-01-02", code="B",
               confidence=0.6, opportunity_score=75, stock_score=60, risk_reward_1=2.0),
        _Trade(entry_executed=True, return_pct=-2.0, date="2024-01-03", code="C",
               confidence=0.5, opportunity_score=70, stock_score=65, risk_reward_1=2.2),
    ]
    panel = build_factor_panel(trades)
    assert len(panel) == 2
    assert set(panel["code"]) == {"A", "C"}
    assert panel["date"].dtype.kind == "M"          # 已解析为时间戳
    assert {"confidence", "opportunity_score", "stock_score", "risk_reward_1"} <= set(panel.columns)
    assert panel["ret"].tolist() == [3.0, -2.0]


def test_build_factor_panel_accepts_dicts_and_drops_bad_rows():
    trades = [
        {"entry_executed": True, "return_pct": 1.0, "date": "2024-01-02", "code": "A",
         "confidence": 0.5},
        {"entry_executed": True, "return_pct": None, "date": "2024-01-03", "code": "B",
         "confidence": 0.6},                       # 缺收益 → 丢弃
        {"entry_executed": True, "return_pct": 2.0, "date": "not-a-date", "code": "C",
         "confidence": 0.7},                       # 日期不可解析 → 丢弃
    ]
    panel = build_factor_panel(trades)
    assert len(panel) == 1
    assert panel["code"].tolist() == ["A"]


def test_build_factor_panel_empty():
    panel = build_factor_panel([])
    assert panel.empty
    assert "ret" in panel.columns


def test_build_factor_panel_supports_custom_factors():
    trades = [_Trade(entry_executed=True, return_pct=1.0, date="2024-01-02", code="A",
                     my_factor=0.9)]
    panel = build_factor_panel(trades, factors=("my_factor",))
    assert "my_factor" in panel.columns
    assert panel["my_factor"].iloc[0] == pytest.approx(0.9)


def test_build_factor_panel_passes_through_extra_columns():
    trades = [_Trade(entry_executed=True, return_pct=1.0, date="2024-01-02", code="A",
                     confidence=0.6, decision="BUY_NOW", exit_reason="target_2")]
    panel = build_factor_panel(trades, extra_cols=("decision", "exit_reason"))
    assert panel["decision"].iloc[0] == "BUY_NOW"
    assert panel["exit_reason"].iloc[0] == "target_2"
    assert "ret" in panel.columns
    # 透传列可支撑分层
    assert not stratified_ic(panel, "confidence", "ret", "decision").empty


# --------------------------------------------------------------------------- #
# 摘要
# --------------------------------------------------------------------------- #
def test_summarize_contains_verdict_and_quantiles():
    panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
    txt = summarize(validate_factor(panel, "factor", "ret"))
    assert "因子 factor" in txt
    assert "IC 均值" in txt
    assert "分位平均收益" in txt
    assert "Q5" in txt


# --------------------------------------------------------------------------- #
# 回归：非数值辅助列 / 成交标记缺失 / 净值时区
# --------------------------------------------------------------------------- #
class TestValidateFactorsColumnInference:
    """回归缺陷：``validate_factors`` 未指定列时把 ``industry`` / ``decision``
    这类字符串辅助列也当因子，直接 ``ValueError`` 让整轮验证失败 —— 而
    ``build_factor_panel(extra_cols=...)`` 的卖点正是「同一面板可分层分析」。"""

    def test_auto_skips_string_columns(self):
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        panel["industry"] = "银行"
        panel["decision"] = "BUY_NOW"
        out = validate_factors(panel, return_col="ret")
        assert out["factor"].tolist() == ["factor"]
        assert "industry" not in out["factor"].tolist()

    def test_explicit_string_column_raises_clear_error(self):
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        panel["industry"] = "银行"
        with pytest.raises(ValueError, match="industry"):
            validate_factors(panel, ["industry"], return_col="ret")

    def test_explicit_missing_column_raises(self):
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        with pytest.raises(ValueError, match="nope"):
            validate_factors(panel, ["nope"], return_col="ret")

    def test_panel_from_build_factor_panel_is_usable_end_to_end(self):
        """``build_factor_panel(extra_cols=...)`` 的面板必须能直接喂 validate_factors。"""
        trades = [
            _Trade(entry_executed=True, return_pct=1.0, date="2024-01-02", code="A",
                   confidence=0.6, decision="BUY_NOW", exit_reason="target_2"),
            _Trade(entry_executed=True, return_pct=-2.0, date="2024-01-03", code="B",
                   confidence=0.3, decision="WATCH", exit_reason="stop_loss"),
        ]
        panel = build_factor_panel(trades, extra_cols=("decision", "exit_reason"))
        out = validate_factors(panel)          # 不应抛 ValueError
        assert "confidence" in out["factor"].tolist()


class TestBuildFactorPanelExecutionFlag:
    """回归缺陷：``require_executed=True`` 只在字段**恰好为 False** 时跳过，
    于是字段缺失 / ``None`` / 非布尔值会被当成「已成交」，把「没买」记成「买了」。"""

    def test_missing_flag_is_excluded(self):
        panel = build_factor_panel([{"return_pct": 5.0, "date": "2024-01-02",
                                     "code": "A", "confidence": 0.7}])
        assert panel.empty

    def test_none_flag_is_excluded(self):
        panel = build_factor_panel([{"entry_executed": None, "return_pct": 5.0,
                                     "date": "2024-01-02", "code": "A",
                                     "confidence": 0.7}])
        assert panel.empty

    def test_string_and_numeric_true_are_accepted(self):
        panel = build_factor_panel([
            {"entry_executed": "true", "return_pct": 1.0, "date": "2024-01-02",
             "code": "A", "confidence": 0.7},
            {"entry_executed": 1, "return_pct": 2.0, "date": "2024-01-02",
             "code": "B", "confidence": 0.8},
        ])
        assert len(panel) == 2

    def test_false_and_garbage_are_excluded(self):
        panel = build_factor_panel([
            {"entry_executed": False, "return_pct": 1.0, "date": "2024-01-02",
             "code": "A", "confidence": 0.7},
            {"entry_executed": "maybe", "return_pct": 2.0, "date": "2024-01-02",
             "code": "B", "confidence": 0.8},
        ])
        assert panel.empty

    def test_require_executed_false_keeps_everything(self):
        panel = build_factor_panel(
            [{"return_pct": 5.0, "date": "2024-01-02", "code": "A", "confidence": 0.7}],
            require_executed=False)
        assert len(panel) == 1


class TestBatchToleratesUnmeasurableFactors:
    """回归缺陷：只要有**任何一个**因子测不出 IC（``ic_mean=None``），
    ``df["ic_mean"].abs()`` 就抛 ``TypeError``，整轮批量验证全废 ——
    而那恰恰是「先看看哪些因子不行」最需要跑通的场景。"""

    def test_constant_factor_does_not_break_batch(self):
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        panel["flat"] = 1.0                       # 恒定 → IC 测不出
        out = validate_factors(panel, return_col="ret")
        assert set(out["factor"]) == {"factor", "flat"}
        assert out.loc[out["factor"] == "flat", "ic_mean"].isna().all()
        # 有效因子必须排在测不出的因子之前
        assert out["factor"].iloc[0] == "factor"

    def test_all_factors_unmeasurable_returns_empty_metrics(self):
        panel = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01"] * 2),
            "code": ["A", "B"],
            "f1": [1.0, 1.0],                     # 恒定
            "ret": [0.1, 0.2],
        })
        out = validate_factors(panel, return_col="ret")
        assert len(out) == 1
        assert out["ic_mean"].iloc[0] is None or pd.isna(out["ic_mean"].iloc[0])

    def test_sorted_by_abs_ic_places_none_last(self):
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        panel["flat"] = 1.0
        out = validate_factors(panel, return_col="ret")
        assert out["factor"].tolist() == ["factor", "flat"]


# --------------------------------------------------------------------------- #
# 判定口径（_verdict / quantile_monotonicity）
#
# 这一层此前只有少量测试：verdict 文案被 factor_weights 按关键词翻译成
# 组合分权重乘数（可用 1.0 / 不单调 0.6 / 不稳定 0.5 / 无区分力 0.0），
# 也就是说文案措辞直接决定「一个维度是否退出排序」。下面把每个分支都钉住。
#
# 复用文件上方已定义的 ``_stats`` / ``_mono``（不要在本文件重复定义同名辅助函数
# —— 后定义会静默覆盖前一个，`tests/test_suite_hygiene.py` 现在会拦住这种写法）。
# --------------------------------------------------------------------------- #
def _qret(means: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "quantile": list(range(1, len(means) + 1)),
        "mean_return": means,
    })


class TestVerdictRules:
    def test_insufficient_sample_is_not_penalised(self):
        v = _verdict(_stats(n=_MIN_PERIODS - 1), _mono(0, 1.0))
        assert "样本不足" in v
        assert classify_verdict(v) == "insufficient"
        assert multiplier_for(v) == 1.0          # 没测出来 ≠ 测出来不行

    def test_significant_zero_ic_is_useless(self):
        """|IC| 很小 **且** t 显著 → 大样本下确认无效 → 清零。"""
        v = _verdict(_stats(ic=0.005, ic_std=0.01, t_stat=2.4), _mono(0, 1.0))
        assert "无区分力" in v
        assert classify_verdict(v) == "useless"
        assert multiplier_for(v) == 0.0

    def test_insignificant_zero_ic_is_only_downgraded(self):
        """|IC| 很小但 t 不显著 → 与「未测出」不可区分 → 只降权不清零。

        回归：实测 ``opportunity_score`` 的 IC=0.017（阈值 0.02）、t=0.20，
        以 0.003 的余量被判「无区分力」，导致整个机会分维度退出排序 ——
        是噪声在替我们做决策。
        """
        v = _verdict(_stats(ic=0.017, ic_std=0.35, t_stat=0.201), _mono(0, 1.0))
        assert "无区分力" not in v
        assert classify_verdict(v) == "unstable"
        assert multiplier_for(v) == 0.5

    def test_zero_ic_without_t_stat_is_useless(self):
        """t 值缺失（每期 IC 完全相同）且 IC≈0 → 确实是「确认无区分力」。"""
        v = _verdict(_stats(ic=0.001, ir=None, ic_std=0.0, t_stat=None), _mono(0, 1.0))
        assert classify_verdict(v) == "useless"

    def test_low_ir_is_unstable(self):
        v = _verdict(_stats(ic=0.10, ir=0.05, ic_std=1.0), _mono(0, 1.0))
        assert "不稳定" in v
        assert multiplier_for(v) == 0.5

    # ----------------------------------------------------------------- #
    # 方向校验（2026-10 裁决 5b）
    #
    # 四个因子都被下游按**正权重**使用。IC 均值为负说明该因子是反向指标 ——
    # 此时判「可用」是危险结论：等于把排序倒过来用，而且"越有效越有害"。
    # 实测 stock_score 的 IC 连续多轮落在 -0.09 ~ -0.11。
    # ----------------------------------------------------------------- #
    def test_negative_ic_is_never_usable(self):
        v = _verdict(_stats(ic=-0.108, ir=-0.31, ic_std=0.35, t_stat=-1.326),
                     _mono(1, -0.7))
        assert "可用" not in v
        assert "方向存疑" in v
        assert classify_verdict(v) == "unstable"     # 不显著 → 降权而非移出
        assert multiplier_for(v) == 0.5

    def test_significant_negative_ic_is_removed(self):
        v = _verdict(_stats(ic=-0.15, ir=-0.50, ic_std=0.30, t_stat=-2.4),
                     _mono(1, -0.7))
        assert "方向相反" in v
        assert classify_verdict(v) == "inverted"
        assert multiplier_for(v) == 0.0

    def test_expected_sign_minus_one_allows_negative_ic(self):
        """因子本身是「越小越好」时（``expected_sign=-1``），负 IC 是正确方向。"""
        v = _verdict(_stats(ic=-0.15, ir=-0.50, ic_std=0.30), _mono(1, -0.7),
                     expected_sign=-1)
        assert "可用" in v

    def test_direction_check_runs_before_monotonicity(self):
        """方向错了就不该因为"分组漂亮"被判可用。"""
        v = _verdict(_stats(ic=-0.20, ir=-0.80, ic_std=0.25),
                     _mono(0, -1.0, down=True))
        assert "可用" not in v

    def test_strict_monotone_is_usable(self):
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(0, 1.0, up=True))
        assert "可用" in v
        assert multiplier_for(v) == 1.0

    def test_quasi_monotone_is_usable(self):
        """1 对逆序 + |ρ| ≥ 0.6 → 准单调，可用。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.7))
        assert "可用" in v
        assert multiplier_for(v) == 1.0

    def test_one_inversion_with_weak_rho_is_not_usable(self):
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.5))
        assert "不单调" in v
        assert multiplier_for(v) == 0.6

    def test_two_inversions_are_not_usable(self):
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(2, 0.9))
        assert "不单调" in v
        assert multiplier_for(v) == 0.6

    def test_unknown_direction_counts_all_pairs_as_inversions(self):
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(None, None))
        assert "不单调" in v

    def test_every_verdict_maps_to_a_known_kind(self):
        """所有分支文案都必须被 factor_weights 认出，不能落到 unknown。"""
        cases = [
            _stats(n=2),
            _stats(ic=0.005, ic_std=0.01, t_stat=2.4),
            _stats(ic=0.017, ic_std=0.35, t_stat=0.2),
            _stats(ic=0.10, ir=0.05, ic_std=1.0),
            _stats(ic=0.15, ic_std=0.25),
            _stats(ic=-0.108, ir=-0.31, ic_std=0.35, t_stat=-1.33),
            _stats(ic=-0.15, ir=-0.50, ic_std=0.30, t_stat=-2.4),
        ]
        monos = [
            _mono(None, None), _mono(0, 1.0, up=True), _mono(1, 0.7),
            _mono(1, 0.5), _mono(3, 0.9), _mono(0, -1.0, down=True),
        ]
        for s in cases:
            for m in monos:
                assert classify_verdict(_verdict(s, m)) != "unknown"


class TestQuantileMonotonicityInversions:
    def test_strictly_increasing_has_no_inversion(self):
        m = quantile_monotonicity(_qret([-2.0, -1.0, 0.0, 1.0, 2.0]))
        assert m["monotone_up"] is True
        assert m["n_inversions"] == 0

    def test_one_inversion_is_counted(self):
        m = quantile_monotonicity(_qret([-2.0, -1.0, -1.5, 1.0, 2.0]))
        assert m["monotone_up"] is False
        assert m["n_inversions"] == 1
        assert m["spearman"] > 0

    def test_two_inversions_are_counted(self):
        m = quantile_monotonicity(_qret([-2.0, -1.0, -1.5, 1.0, 0.5]))
        assert m["n_inversions"] == 2

    def test_strictly_decreasing_direction(self):
        m = quantile_monotonicity(_qret([2.0, 1.0, 0.0, -1.0, -2.0]))
        assert m["monotone_down"] is True
        assert m["n_inversions"] == 0

    def test_empty_returns_none_inversions(self):
        m = quantile_monotonicity(pd.DataFrame({"quantile": [], "mean_return": []}))
        assert m["n_inversions"] is None


# --------------------------------------------------------------------------- #
# 样本外稳健性门槛（2026-10 裁决：由「仅报告」升级为 verdict 的必要条件）
#
# 条件②要求「可用因子符号稳定」。全样本 IC 为正不代表样本外仍为正 ——
# 「符号稳定」只能由滚动前瞻回答。原实现里 walk_forward_factor 的结果只写进报告、
# 从不参与判定，等于让这条要求形同虚设。
#
# 门槛：折数 ≥ _WF_MIN_FOLDS 时，同号折占比需 ≥ 75%（4 折 ⇒ 至少 3 折同号）；
# 折数不足时**不启用**（「没测出来」≠「测出来不稳」，不能拿样本不足当反对票）。
# --------------------------------------------------------------------------- #
class TestWalkForwardGate:
    def test_without_walk_forward_behaviour_is_unchanged(self):
        """向后兼容：不传 walk_forward 时判定与旧口径完全一致。"""
        assert _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.7)).startswith("可用")
        assert "不单调" in _verdict(_stats(ic=0.15, ic_std=0.25), _mono(2, 0.9))

    def test_two_of_four_folds_downgrades_to_unstable(self):
        """IC/IR/单调性全达标，但样本外只有 2/4 折同号 → 不判可用。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.7),
                     walk_forward={"n_folds": 4, "n_sign_kept": 2})
        assert "可用" not in v
        assert "样本外不稳健" in v
        assert classify_verdict(v) == "unstable"
        assert multiplier_for(v) == 0.5

    def test_three_of_four_folds_is_usable(self):
        """4 折里 3 折同号 = 恰好达到 75% 门槛 → 可用。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.7),
                     walk_forward={"n_folds": 4, "n_sign_kept": 3})
        assert v.startswith("可用")

    def test_two_of_three_folds_is_not_enough(self):
        """3 折里 2 折 = 66.7% < 75% → 不达标（门槛按折数向上取整）。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(0, 1.0, up=True),
                     walk_forward={"n_folds": 3, "n_sign_kept": 2})
        assert "样本外不稳健" in v

    def test_too_few_folds_does_not_penalise(self):
        """折数不足 → 门槛不启用（与「样本不足不惩罚」同一原则）。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(1, 0.7),
                     walk_forward={"n_folds": 2, "n_sign_kept": 0})
        assert v.startswith("可用")

    def test_walk_forward_does_not_rescue_failing_factor(self):
        """样本外达标也不能救一个 IC/单调性本身不达标的因子（判定顺序不可颠倒）。"""
        v = _verdict(_stats(ic=0.15, ic_std=0.25), _mono(3, 0.9),
                     walk_forward={"n_folds": 4, "n_sign_kept": 4})
        assert "不单调" in v

    def test_summary_counts_kept_folds(self):
        wf = pd.DataFrame({"fold": [1, 2, 3, 4],
                           "sign_kept": [True, False, True, True]})
        s = walk_forward_summary(wf)
        assert s == {"n_folds": 4, "n_sign_kept": 3, "kept_ratio": 0.75}

    def test_summary_of_empty_is_zero(self):
        assert walk_forward_summary(pd.DataFrame())["n_folds"] == 0

    def test_validate_factor_accepts_walk_forward(self):
        """``validate_factor`` 传入滚动前瞻后，verdict 必须反映样本外结果。"""
        panel = _panel(n_dates=12, n_names=20, signal=5.0, noise=0.5)
        wf = pd.DataFrame({"fold": [1, 2, 3, 4],
                           "sign_kept": [False, False, False, False]})
        rep = validate_factor(panel, "factor", "ret", walk_forward=wf)
        assert rep["walk_forward"]["n_sign_kept"] == 0
        assert "样本外不稳健" in rep["verdict"]
        # 不传时保持旧口径（合成强因子必然可用）
        assert validate_factor(panel, "factor", "ret")["verdict"].startswith("可用")

    def test_validate_factors_reports_wf_kept_column(self):
        """批量汇总表必须带 ``wf_kept`` 列，让「样本外几折同号」在报告里可见。"""
        panel = _panel(n_dates=24, n_names=20, signal=5.0, noise=0.5)
        out = validate_factors(panel, ["factor"], return_col="ret")
        assert "wf_kept" in out.columns
        assert out["wf_kept"].iloc[0] != ""          # 折数足够 → 有值
        assert out["verdict"].iloc[0].startswith("可用")

    def test_validate_factors_can_skip_walk_forward(self):
        panel = _panel(n_dates=24, n_names=20, signal=5.0, noise=0.5)
        out = validate_factors(panel, ["factor"], return_col="ret", walk_forward=False)
        assert out["wf_kept"].iloc[0] == ""
