"""置信度概率标定单元测试（纯离线，不联网）。

覆盖 calibration 模块的数学正确性、数值稳健性与安全阀，以及引擎接入后的
「排序与决策不变」这一关键不变量。
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.backtest.trading_plan_backtest import BacktestTrade
from quant_trading_system.stock_analysis.calibration import (
    Calibrator,
    IsotonicParams,
    PlattParams,
    auc_score,
    brier_score,
    default_calibrator_path,
    ece_score,
    fit_calibrator,
    isotonic_apply,
    isotonic_fit,
    platt_apply,
    platt_fit,
    reliability_table,
)
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _perfect_sample(n: int = 20000, seed: int = 0):
    """构造一个「打分即真实概率」的样本，ECE 应当 ≈ 0。"""
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.0, 1.0, n)
    y = (rng.uniform(0.0, 1.0, n) < p).astype(float)
    return p, y


def _platt_sample(a_true: float, n: int = 40000, seed: int = 7):
    """构造真实标定关系为 p = σ(a_true · logit(s)) 的样本。"""
    rng = np.random.default_rng(seed)
    s = rng.uniform(0.05, 0.95, n)
    lg = np.log(s / (1 - s))
    p_true = 1.0 / (1.0 + np.exp(-a_true * lg))
    y = (rng.uniform(0.0, 1.0, n) < p_true).astype(float)
    return s, y


def _kline(n: int = 200, seed: int = 42, trend: float = 0.03, vol: float = 0.12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def test_ece_zero_for_perfectly_calibrated():
    p, y = _perfect_sample()
    assert ece_score(p, y) < 0.02
    assert 0.14 < brier_score(p, y) < 0.19      # ≈ E[p(1-p)] ≈ 1/6


def test_ece_large_for_overconfident_scores():
    p, y = _perfect_sample()
    s = np.clip(0.5 + (p - 0.5) * 3.0, 0.01, 0.99)   # 放大 → 过度自信
    assert ece_score(s, y) > 0.10
    assert ece_score(s, y) > ece_score(p, y)


def test_ece_handles_degenerate_and_boundary_input():
    y = np.array([1.0, 0.0, 1.0, 0.0])
    # 0/1 打分会被裁剪到 [1e-6, 1-1e-6]（logit 需要），故 ECE 是 1e-6 量级而非精确 0
    assert ece_score([1.0, 0.0, 1.0, 0.0], y) == pytest.approx(0.0, abs=1e-4)
    assert ece_score([0.0, 0.0, 0.0, 0.0], y) > 0.0
    assert ece_score([1.0] * 4, y) > 0.0


def test_reliability_table_shape_and_empty_bins():
    p, y = _perfect_sample(n=500)
    rows = reliability_table(p, y, n_bins=5)
    assert len(rows) == 5
    assert sum(r["count"] for r in rows) == 500
    assert all(set(r) == {"bin", "left", "right", "count", "mean_pred", "actual_freq", "gap"}
               for r in rows)
    # 空分箱的均值字段必须是 None，而不是 NaN 或 0
    empty = [r for r in rows if r["count"] == 0]
    assert all(r["mean_pred"] is None and r["gap"] is None for r in empty)


def test_metrics_reject_bad_input():
    with pytest.raises(ValueError):
        ece_score([0.1, 0.2], [1.0])              # 长度不一致
    with pytest.raises(ValueError):
        ece_score([0.1, float("nan")], [0.0, 1.0])  # NaN
    with pytest.raises(ValueError):
        ece_score([0.1, 0.2], [0.0, 0.5])         # 非 0/1 标签


# --------------------------------------------------------------------------- #
# Platt
# --------------------------------------------------------------------------- #
def test_platt_recovers_known_slope():
    for a_true in (0.35, 0.6, 1.0, 1.8):
        s, y = _platt_sample(a_true)
        fit = platt_fit(s, y)
        assert fit.a == pytest.approx(a_true, abs=0.05)
        assert abs(fit.b) < 0.1
        assert ece_score(platt_apply(s, fit), y) < 0.02


def test_platt_does_not_diverge_on_quasi_separated_data():
    """回归测试：无约束 Newton 曾在这类数据上跑飞到 a≈1.5e8。

    打分与结果近乎可分时 logistic 的极大似然解在无穷远；修好之后斜率必须落在
    有意义的范围内，且标定后的 ECE 不得比标定前更差。
    """
    rng = np.random.default_rng(0)
    p = rng.uniform(0.0, 1.0, 20000)
    y = (rng.uniform(0.0, 1.0, 20000) < p).astype(float)
    s = np.clip(0.5 + (p - 0.5) * 3.0, 0.01, 0.99)

    fit = platt_fit(s, y)
    assert 0.0 < fit.a < 10.0
    assert abs(fit.b) < 30.0
    assert ece_score(platt_apply(s, fit), y) <= ece_score(s, y)


def test_platt_is_monotone_non_decreasing():
    s = np.linspace(0.0, 1.0, 400)
    for a in (0.1, 0.5, 1.0, 3.0):
        out = platt_apply(s, PlattParams(a=a, b=0.3))
        assert np.all(np.diff(out) >= -1e-12)


def test_platt_free_slope_false_fits_only_offset():
    """回归测试：固定斜率曾是错的——x 被当成设计列，实际拟合 σ(b·x)，斜率锁在 0。

    症状是同一份数据自由斜率给出 b≈0.01，固定斜率却给出 b≈1.0（后者把 x 乘进
    了斜率位，等价于 a=1 的正确模型）。修好后两条路径的截距必须接近。
    """
    s, y = _platt_sample(1.0)
    free = platt_fit(s, y)
    fixed = platt_fit(s, y, free_slope=False)

    assert fixed.a == 1.0
    assert abs(fixed.b) < 0.1
    # 两种参数化描述同一个模型时，截距应当一致
    assert fixed.b == pytest.approx(free.b, abs=0.05)
    # 固定斜率的 a=1 路径必须真的把概率还原成 s 本身
    assert platt_apply([0.8], fixed)[0] == pytest.approx(0.8, abs=0.05)


def test_platt_boundary_scores_are_finite():
    fit = platt_fit([0.0, 0.5, 1.0] * 40, [0.0, 1.0] * 60)
    out = platt_apply([0.0, 1.0, 0.5], fit)
    assert np.all(np.isfinite(out))
    assert np.all((out >= 0.0) & (out <= 1.0))


# --------------------------------------------------------------------------- #
# Isotonic
# --------------------------------------------------------------------------- #
def test_pava_produces_monotone_output_of_same_length():
    v = np.array([0.1, 0.5, 0.2, 0.6, 0.7, 0.3])
    fit = isotonic_fit(v, (v > 0.4).astype(float), min_leaf=1)
    assert np.all(np.diff(fit.ys) >= -1e-12)
    assert len(fit.xs) == len(fit.ys) == len(fit.counts)


def test_isotonic_apply_is_monotone_and_clamps_outside_range():
    s, y = _platt_sample(0.6, n=5000)
    fit = isotonic_fit(s, y, min_leaf=20)
    grid = np.linspace(0.0, 1.0, 500)
    out = isotonic_apply(grid, fit)
    assert np.all(np.diff(out) >= -1e-12)
    # 超出节点范围时夹在两端
    assert isotonic_apply([-1.0], fit)[0] == pytest.approx(fit.ys[0])
    assert isotonic_apply([2.0], fit)[0] == pytest.approx(fit.ys[-1])


def test_isotonic_min_leaf_merges_tiny_steps():
    # 每档只有 1~2 个样本时，min_leaf 应该把它们合并掉
    s = np.repeat(np.linspace(0, 1, 50), 2)
    y = (np.arange(100) % 3 == 0).astype(float)
    coarse = isotonic_fit(s, y, min_leaf=20)
    fine = isotonic_fit(s, y, min_leaf=1)
    assert len(coarse.xs) <= len(fine.xs)


def test_isotonic_empty_params_falls_back_to_identity():
    out = isotonic_apply([0.2, 0.8], IsotonicParams())
    assert out.tolist() == pytest.approx([0.2, 0.8])


# --------------------------------------------------------------------------- #
# Calibrator 与 fit_calibrator
# --------------------------------------------------------------------------- #
def test_fit_calibrator_improves_ece_and_stays_monotone():
    s, y = _platt_sample(0.35)
    cal, rep = fit_calibrator(s, y, method="platt")
    assert rep["ece_after_cv"] < rep["ece_before"]
    assert rep["brier_after_cv"] < rep["brier_before"]
    # 排序不变的不变量
    grid = np.linspace(0.0, 1.0, 300)
    assert np.all(np.diff(cal.apply_many(grid)) >= -1e-12)


def test_fit_calibrator_auto_switches_method_by_sample_size():
    s_small, y_small = _platt_sample(0.5, n=400)
    assert fit_calibrator(s_small, y_small, method="auto")[1]["method"] == "platt"

    s_big, y_big = _platt_sample(0.5, n=1500)
    assert fit_calibrator(s_big, y_big, method="auto")[1]["method"] == "isotonic"


def test_fit_calibrator_falls_back_on_single_class_labels():
    cal, rep = fit_calibrator([0.2, 0.5, 0.8] * 20, [0.0] * 60)
    assert rep["method"] == "identity"
    assert cal.method == "identity"
    assert "单一类别" in rep["fell_back"]


def test_fit_calibrator_falls_back_on_too_few_samples():
    cal, rep = fit_calibrator([0.2, 0.5], [0.0, 1.0])
    assert cal.method == "identity"
    assert "不足以标定" in rep["fell_back"]


def test_fit_calibrator_never_returns_worse_than_raw():
    """安全阀：CV 指标劣于原始打分的标定器必须被拒绝。"""
    s, y = _perfect_sample(n=3000)
    _, rep = fit_calibrator(s, y, method="platt")
    if rep.get("rejected_method"):
        assert rep["method"] == "identity"
    assert rep["ece_after_cv"] <= rep["ece_before"] + 1e-9


def test_fit_calibrator_uses_time_block_cv_when_explicitly_requested():
    s, y = _platt_sample(0.5, n=1000)
    # 显式 time_block：训练集包含测试折之外的所有折（旧口径，仅作对照）
    _, rep = fit_calibrator(s, y, order=np.arange(1000), cv="time_block")
    assert rep["cv_strategy"] == "time_block"
    # 不给 order 时默认随机折
    _, rep_rand = fit_calibrator(s, y)
    assert rep_rand["cv_strategy"] == "random"


def test_fit_calibrator_defaults_to_walk_forward_when_order_given():
    """给了时间序时，默认必须走滚动前瞻（训练集严格早于测试集），防前视泄漏。"""
    s, y = _platt_sample(0.5, n=1000)
    _, rep = fit_calibrator(s, y, order=np.arange(1000))
    assert rep["cv_strategy"] == "walk_forward"
    assert rep["cv_embargo"] == 0
    # 第 0 段没有可用训练集 → 只覆盖后 k-1 段，覆盖率必然 < 100%
    assert 0 < rep["cv_covered"] < 1000


def test_walk_forward_folds_never_train_on_future():
    """核心不变量：每折训练索引必须全部早于测试索引。"""
    from quant_trading_system.stock_analysis.calibration import _walk_forward_folds

    order = np.arange(100)              # 严格按时间排列
    folds = _walk_forward_folds(100, 5, order, embargo=0)
    assert len(folds) == 4              # 5 段 → 4 个可用折
    for train, test in folds:
        assert train.size >= 2
        assert test.size > 0
        assert train.max() < test.min(), "训练集混入了未来样本"


def test_walk_forward_embargo_drops_recent_training_rows():
    from quant_trading_system.stock_analysis.calibration import _walk_forward_folds

    order = np.arange(100)
    no_gap = _walk_forward_folds(100, 5, order, embargo=0)
    with_gap = _walk_forward_folds(100, 5, order, embargo=5)
    for (tr0, te0), (tr1, te1) in zip(no_gap, with_gap):
        assert tr1.size == tr0.size - 5
        # 隔离后训练集仍必须严格早于测试集
        assert tr1.max() < te1.min()
        assert np.array_equal(te0, te1)      # 隔离只动训练集，不动测试集


def test_fit_calibrator_rejects_unknown_method():
    s, y = _platt_sample(0.5, n=200)
    with pytest.raises(ValueError):
        fit_calibrator(s, y, method="nope")


def test_auc_score_basics():
    # 完全可分 → 1.0
    assert auc_score([0.1, 0.2, 0.3, 0.8, 0.9], [0, 0, 0, 1, 1]) == pytest.approx(1.0)
    # 完全反序 → 0.0（注意不是 0.5：这能发现「打分方向搞反了」）
    assert auc_score([0.9, 0.8, 0.7, 0.2, 0.1], [0, 0, 0, 1, 1]) == pytest.approx(0.0)
    # 全部并列 → 0.5，且不因并列而虚高
    assert auc_score([0.5] * 10, [0, 1] * 5) == pytest.approx(0.5)
    # 无区分力的随机打分 → 接近 0.5
    rng = np.random.default_rng(3)
    assert 0.40 < auc_score(rng.uniform(0, 1, 4000), rng.integers(0, 2, 4000).astype(float)) < 0.60
    # 单一类别无法定义 AUC
    assert math.isnan(auc_score([0.1, 0.2], [1, 1]))


def test_auc_score_matches_brute_force_with_ties():
    """并列（引擎会把 confidence 取整到 2 位小数 → 大量并列）必须用平均秩。

    与暴力配对法交叉验证：AUC = (胜对数 + 0.5 × 并列对数) / 总对数。
    """
    rng = np.random.default_rng(5)
    s = rng.choice([0.2, 0.5, 0.8], size=400).astype(float)
    y = rng.integers(0, 2, 400).astype(float)

    pos, neg = s[y == 1], s[y == 0]
    wins = float((pos[:, None] > neg[None, :]).sum())
    ties = float((pos[:, None] == neg[None, :]).sum())
    expected = (wins + 0.5 * ties) / (pos.size * neg.size)
    assert 0.0 < expected < 1.0

    assert auc_score(s, y) == pytest.approx(expected)


def test_fit_calibrator_rejects_score_that_collapses():
    """塌缩守卫：无信号打分会被 Platt 压成常数（ECE 反而很好），必须否决。

    这是真实数据上踩到的：置信度与盈亏无关时，Platt 最优解是「所有人都输出基础
    成功率」——ECE 从 0.36 降到 0.10 漂亮得很，但所有标的显示同一个数。
    """
    rng = np.random.default_rng(21)
    y = rng.integers(0, 2, 600).astype(float)
    s = rng.uniform(0.4, 0.95, 600)          # 与 y 完全无关
    cal, rep = fit_calibrator(s, y, method="platt")

    assert rep["method"] == "identity"
    assert cal.method == "identity"
    assert rep["rejected_method"] == "platt"
    assert "压塌" in rep["fell_back"] or "区分力" in rep["fell_back"]
    # 交付 identity 时指标必须与标定前一致
    assert rep["ece_after_cv"] == rep["ece_before"]
    assert rep["spread_after_cv"] == rep["spread_before"]
    # 诊断：AUC 接近 0.5 必须被指出来
    assert rep["diagnosis"] and "区分力" in rep["diagnosis"]


def test_delivered_calibrator_never_collapses_under_walk_forward():
    """塌缩守卫必须检查**实际交付的**标定器，而不是 out-of-fold 预测。

    真实场景：walk_forward 每折各学一个略不同的常数，oof 跨度看着正常，但全样本
    拟合出的映射把所有输入压成同一个值——上线后所有标的显示同一置信度。
    不变量：要么被否决（method=identity），要么交付的映射确有区分度。
    """
    rng = np.random.default_rng(7)
    n = 300
    y = rng.integers(0, 2, n).astype(float)          # 与打分无关 → 应被压塌
    s = rng.uniform(0.4, 0.95, n)
    cal, rep = fit_calibrator(s, y, method="platt", order=np.arange(n))

    if rep["method"] == "identity":
        assert cal.method == "identity"
        assert rep.get("rejected_method")
    else:
        out = cal.apply_many(np.linspace(0.0, 1.0, 200))
        assert out.max() - out.min() >= 0.05, "交付的标定器把置信度压成了常数"
    assert rep["spread_delivered"] >= 0.0


def test_fit_calibrator_keeps_discrimination_for_usable_score():
    """有区分力的打分：标定后 AUC 不得下降（这是它敢上线的前提）。"""
    s, y = _platt_sample(0.4, n=4000)
    cal, rep = fit_calibrator(s, y, method="platt")
    assert rep["method"] == "platt"
    assert rep["auc_after_cv"] >= rep["auc_before"] - 0.02
    assert rep["spread_after_cv"] >= 0.05
    assert "diagnosis" not in rep


def test_calibrator_roundtrip_and_missing_file(tmp_path):
    s, y = _platt_sample(0.4, n=2000)
    cal, _ = fit_calibrator(s, y, method="platt")
    fp = tmp_path / "sub" / "cal.json"
    cal.save(fp)
    assert fp.exists()
    assert json.loads(fp.read_text(encoding="utf-8"))["method"] == "platt"

    loaded = Calibrator.load(fp)
    grid = np.linspace(0.0, 1.0, 200)
    assert loaded is not None
    # 参数在 JSON 里保留 6 位小数，往返有 1e-6 量级的舍入，不影响使用
    assert loaded.apply_many(grid) == pytest.approx(cal.apply_many(grid), rel=1e-4, abs=1e-6)

    assert Calibrator.load(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert Calibrator.load(broken) is None


def test_calibrator_apply_handles_none_and_extremes():
    cal = Calibrator("platt", platt=PlattParams(a=0.5, b=0.0))
    assert cal.apply(None) is None
    assert 0.0 <= cal.apply(0.0) <= 1.0
    assert 0.0 <= cal.apply(1.0) <= 1.0
    assert Calibrator("identity").apply(0.7) == pytest.approx(0.7)


def test_load_default_calibrator_rejects_synthetic_source(tmp_path, monkeypatch):
    import quant_trading_system.stock_analysis.calibration as mod

    good = tmp_path / "confidence_calibration.json"
    Calibrator("platt", platt=PlattParams(a=0.5, b=0.0),
               meta={"source": "真实数据 600519"}).save(good)
    monkeypatch.setattr(mod, "default_calibrator_path", lambda: good)
    assert mod.load_default_calibrator() is not None

    Calibrator("platt", platt=PlattParams(a=0.5, b=0.0),
               meta={"source": "合成数据(离线)"}).save(good)
    assert mod.load_default_calibrator() is None
    assert mod.load_default_calibrator(allow_synthetic=True) is not None

    monkeypatch.setattr(mod, "default_calibrator_path", lambda: tmp_path / "absent.json")
    assert mod.load_default_calibrator() is None


def test_default_calibrator_path_points_at_repo_results(monkeypatch):
    monkeypatch.delenv("QTS_DATA_DIR", raising=False)
    p = default_calibrator_path()
    assert p.name == "confidence_calibration.json"
    assert p.parent.name == "results"


def test_default_calibrator_path_follows_qts_data_dir(monkeypatch, tmp_path):
    """打包运行时 QTS_DATA_DIR 指向 <exe>/config，标定文件必须落在它的同级 results/。

    否则引擎会去 PyInstaller 解包目录找参数，永远读不到用户自备的标定文件。
    """
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "config"))
    assert default_calibrator_path() == tmp_path / "results" / "confidence_calibration.json"

    # 不以 config 结尾时直接用该目录
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path / "data"))
    assert default_calibrator_path() == tmp_path / "data" / "results" / "confidence_calibration.json"


# --------------------------------------------------------------------------- #
# 回测标签
# --------------------------------------------------------------------------- #
def _trade(**kw) -> BacktestTrade:
    base = dict(entry_executed=True, exit_reason="target_2", return_pct=5.0, confidence=0.7)
    base.update(kw)
    return BacktestTrade(**base)


def test_outcome_label_basics():
    assert _trade(return_pct=5.0).outcome_label() == 1
    assert _trade(return_pct=-3.0).outcome_label() == 0


def test_outcome_label_excludes_not_entered():
    assert _trade(entry_executed=False, exit_reason="not_entered",
                  return_pct=0.0).outcome_label() is None


def test_outcome_label_excludes_dead_zone_noise():
    assert _trade(return_pct=0.29).outcome_label(dead_zone=0.3) is None
    assert _trade(return_pct=-0.29).outcome_label(dead_zone=0.3) is None
    assert _trade(return_pct=0.31).outcome_label(dead_zone=0.3) == 1
    assert _trade(return_pct=-0.31).outcome_label(dead_zone=0.3) == 0
    # dead_zone=0 时不做噪声过滤
    assert _trade(return_pct=0.01).outcome_label(dead_zone=0.0) == 1


def test_outcome_label_excludes_missing_confidence():
    assert _trade(confidence=None).outcome_label() is None


# --------------------------------------------------------------------------- #
# 引擎接入
# --------------------------------------------------------------------------- #
def test_engine_without_calibrator_keeps_legacy_behaviour():
    df = _kline(seed=3)
    res = OpportunityEngine(account_equity=100_000, regime_score=65).analyze("T", "T", df)
    assert res.plan is not None
    # 未配置标定器时不得写入 confidence_raw，行为与历史一致
    assert "confidence_raw" not in res.plan.meta


def test_engine_calibrator_changes_value_but_not_decision():
    df = _kline(seed=3)
    base = OpportunityEngine(account_equity=100_000, regime_score=65).analyze("T", "T", df)
    cal = Calibrator("platt", platt=PlattParams(a=0.3, b=-0.8))
    tuned = OpportunityEngine(account_equity=100_000, regime_score=65,
                              calibrator=cal).analyze("T", "T", df)

    assert base.plan is not None and tuned.plan is not None
    assert tuned.plan.decision == base.plan.decision
    # 原始打分被完整保留在 meta 里
    assert tuned.plan.meta["confidence_raw"] == base.plan.confidence
    # 0.3 斜率 + 负截距会把置信度显著压低
    assert tuned.plan.confidence < base.plan.confidence


def test_engine_calibrator_preserves_ranking_across_stocks():
    """标定单调 → 跨票排序必须完全一致（这是它敢上线的前提）。"""
    engines = [
        OpportunityEngine(account_equity=100_000, regime_score=65),
        OpportunityEngine(account_equity=100_000, regime_score=65,
                          calibrator=Calibrator("platt", platt=PlattParams(a=0.25, b=-1.0))),
    ]
    confs = [[], []]
    for seed in range(1, 8):
        df = _kline(seed=seed)
        for k, eng in enumerate(engines):
            res = eng.analyze(f"S{seed}", f"S{seed}", df)
            confs[k].append(res.plan.confidence if res.plan else -1.0)

    order_plain = np.argsort(confs[0])
    order_cal = np.argsort(confs[1])
    assert order_plain.tolist() == order_cal.tolist()


def test_backtest_records_confidence_snapshot():
    """回测必须记录打分快照，否则标定拿不到配对样本。"""
    df = _kline(n=400, seed=5)
    engine = OpportunityEngine(account_equity=100_000, regime_score=65)
    res = TradingPlanBacktest(engine=engine, stride=20).run(df, "T", "T")
    assert res.trades, "合成数据应能产出交易计划"
    for t in res.trades:
        assert t.confidence is not None
        assert t.opportunity_score is not None
        assert t.stock_score is not None
        assert t.risk_reward_1 is not None
        # 标签可算且落在合法集合内
        assert t.outcome_label() in (0, 1, None)


def test_backtest_records_raw_score_even_with_calibrated_engine():
    """防循环：回测必须记录未标定打分，否则会「用已标定分数再拟合标定」。

    引擎启用标定器后会把原始值写进 meta["confidence_raw"]，回测应当优先取它。
    """
    df = _kline(n=400, seed=5)
    cal = Calibrator("platt", platt=PlattParams(a=0.25, b=-1.0))
    plain = TradingPlanBacktest(
        engine=OpportunityEngine(account_equity=100_000, regime_score=65), stride=20
    ).run(df, "T", "T")
    tuned = TradingPlanBacktest(
        engine=OpportunityEngine(account_equity=100_000, regime_score=65, calibrator=cal),
        stride=20,
    ).run(df, "T", "T")

    assert len(plain.trades) == len(tuned.trades) > 0
    for a, b in zip(plain.trades, tuned.trades):
        assert a.confidence == b.confidence          # 两边记录的都是原始打分
        assert b.confidence is not None


def test_backtest_records_parseable_date_from_datetime_index():
    """回归测试：fetch_kline 返回 DatetimeIndex、没有 date 列。

    若只判断 ``"date" in df.columns``，所有交易日期会退化成 K 线序号，多股票拼接后
    时间序错乱，时序分块交叉验证会退化成「按股票分块」。日期必须可解析。
    """
    n = 320
    rng = np.random.default_rng(11)
    close = 10 + np.cumsum(rng.normal(0.02, 0.13, n))
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "volume": rng.uniform(1e6, 5e6, n)},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )
    assert "date" not in df.columns
    df = add_all_indicators(df)

    res = TradingPlanBacktest(
        engine=OpportunityEngine(account_equity=100_000, regime_score=65), stride=20
    ).run(df, "T", "T")
    assert res.trades
    parsed = [pd.Timestamp(t.date) for t in res.trades]   # 不可解析会抛异常
    # 回归点：曾静默退化成序号 → pd.Timestamp("0") == 1970-01-01
    assert all(p.year >= 2024 for p in parsed), f"日期退化: {[t.date for t in res.trades][:3]}"
    # 时间序必须单调递增，否则跨股票拼接后时序分块失去意义
    assert all(b >= a for a, b in zip(parsed, parsed[1:]))


def test_backtest_labels_are_usable_for_fitting():
    """端到端：回测产出 → 收集样本 → 拟合不报错、指标合理。

    2026-09 修正成交可达性 + 加入交易成本后，可成交样本显著减少（这是预期结果，
    不是回归）。这里改成「按需扩样本」而不是固定 5 组，避免阈值随口径变化而脆断。
    """
    trades = []
    scores, labels = [], []
    for seed in range(1, 13):
        df = _kline(n=420, seed=seed)
        engine = OpportunityEngine(account_equity=100_000, regime_score=65)
        trades.extend(TradingPlanBacktest(engine=engine, stride=10).run(df, "T", "T").trades)
        scores, labels = [], []
        for t in trades:
            lbl = t.outcome_label()
            if lbl is not None:
                scores.append(t.confidence)
                labels.append(lbl)
        if len(scores) >= 40:
            break

    assert len(scores) >= 30, f"样本量不足（{len(scores)}），无法验证标定链路"
    assert set(labels) <= {0, 1}

    cal, rep = fit_calibrator(scores, labels, method="platt")
    assert rep["n_samples"] == len(scores)
    assert 0.0 <= rep["ece_before"] <= 1.0
    assert 0.0 <= rep["ece_after_cv"] <= 1.0
    # 无论是否启用，标定后都不该比原始打分更差
    assert rep["ece_after_cv"] <= rep["ece_before"] + 1e-9
    assert cal.apply(0.5) is not None
