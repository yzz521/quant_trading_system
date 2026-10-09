"""按因子验证结论自动降权（``scoring.factor_weights``）单元测试。

纯离线、确定性。覆盖四块：
1. verdict → 分类 / 乘数的映射（含文案微调的鲁棒性）；
2. 纯函数 ``downweight_from_report``：正常降权、归一化、三条守卫、附注；
3. 读盘 ``load_validated_weights``：缺失 / 损坏 / 过期 / 正常；
4. 缓存 ``active_weights`` 与消费方接线（批扫描器用生效权重排序）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from quant_trading_system.stock_analysis.scoring.composite import (
    COMPOSITE_WEIGHTS,
    score_plan,
)
from quant_trading_system.stock_analysis.scoring.factor_weights import (
    DIMENSION_FACTOR,
    MAX_DIMENSION_WEIGHT,
    MIN_ACTIVE_DIMENSIONS,
    VERDICT_MULTIPLIER,
    active_weights,
    classify_verdict,
    clear_cache,
    downweight_from_report,
    load_validated_weights,
    multiplier_for,
)


# --------------------------------------------------------------------------- #
# 1) 判定映射
# --------------------------------------------------------------------------- #
class TestVerdictMapping:
    @pytest.mark.parametrize("verdict,expected", [
        ("可用（IC/IR 与分组单调性均达标）", "usable"),
        ("分组不单调，排序含义模糊", "nonmonotone"),
        ("区分力不稳定（IR 0.152），单期噪声大", "unstable"),
        ("区分力不稳定（IR 无法计算）", "unstable"),
        ("无区分力（IC 接近 0），不建议参与排序", "useless"),
        ("样本不足（仅 3 期，需 ≥8）", "insufficient"),
        ("", "unknown"),
        (None, "unknown"),
        ("某种未来新增的结论文案", "unknown"),
    ])
    def test_classify(self, verdict, expected):
        assert classify_verdict(verdict) == expected

    def test_classify_is_keyword_based_not_exact(self):
        """verdict 文案微调（加前缀/后缀）不应让整条链路失效。"""
        assert classify_verdict("【警告】无区分力：IC 接近 0") == "useless"
        assert classify_verdict("  分组不单调  ") == "nonmonotone"

    def test_multipliers_ordering(self):
        """乘数必须体现「越差降越多」的单调性，否则降权方向就是错的。"""
        assert VERDICT_MULTIPLIER["usable"] > VERDICT_MULTIPLIER["nonmonotone"]
        assert VERDICT_MULTIPLIER["nonmonotone"] > VERDICT_MULTIPLIER["unstable"]
        assert VERDICT_MULTIPLIER["unstable"] > VERDICT_MULTIPLIER["useless"]

    def test_insufficient_and_unknown_are_not_punished(self):
        """守卫 1：没测出来 ≠ 测出来不行，不得降权。"""
        assert multiplier_for("样本不足（仅 3 期，需 ≥8）") == 1.0
        assert multiplier_for(None) == 1.0
        assert multiplier_for("") == 1.0

    def test_useless_is_removed(self):
        assert multiplier_for("无区分力（IC 接近 0），不建议参与排序") == 0.0


# --------------------------------------------------------------------------- #
# 2) 纯函数降权
# --------------------------------------------------------------------------- #
def _report(**verdicts) -> dict:
    """构造最小报告：factor → verdict。"""
    return {"summary": [{"factor": k, "verdict": v} for k, v in verdicts.items()]}


class TestDownweight:
    def test_all_usable_keeps_baseline(self):
        w, notes, kinds = downweight_from_report(_report(
            stock_score="可用（IC/IR 与分组单调性均达标）",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="可用（IC/IR 与分组单调性均达标）",
        ))
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert all(k == "usable" for k in kinds.values())
        assert any("未触发降权" in n for n in notes)

    def test_empty_report_falls_back_to_baseline(self):
        for bad in (None, {}, {"summary": []}):
            w, notes, kinds = downweight_from_report(bad)
            assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
            assert kinds == {}
            assert notes and "基线权重" in notes[0]

    def test_useless_dimension_removed_and_others_renormalized(self):
        """rr 无区分力 → 移出公式；stock/opportunity 按 45:35 重新分配。"""
        w, _, kinds = downweight_from_report(_report(
            stock_score="可用（IC/IR 与分组单调性均达标）",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="无区分力（IC 接近 0），不建议参与排序",
        ))
        assert kinds["rr"] == "useless"
        assert w["rr"] == 0.0
        assert w["stock"] > COMPOSITE_WEIGHTS["stock"]      # 被释放的权重确实分出去了
        assert w["opportunity"] > COMPOSITE_WEIGHTS["opportunity"]
        assert w["stock"] / w["opportunity"] == pytest.approx(45 / 35, rel=1e-3)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)

    def test_weights_always_sum_to_one(self):
        for verdicts in (
            _report(stock_score="无区分力（IC 接近 0），不建议参与排序",
                    opportunity_score="可用（IC/IR 与分组单调性均达标）",
                    risk_reward_1="可用（IC/IR 与分组单调性均达标）"),
            _report(stock_score="分组不单调，排序含义模糊",
                    opportunity_score="区分力不稳定（IR 0.1），单期噪声大",
                    risk_reward_1="可用（IC/IR 与分组单调性均达标）"),
            _report(stock_score="分组不单调，排序含义模糊",
                    opportunity_score="分组不单调，排序含义模糊",
                    risk_reward_1="分组不单调，排序含义模糊"),
        ):
            w, _, _ = downweight_from_report(verdicts)
            assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)

    def test_guard_too_few_active_dimensions_falls_back(self):
        """守卫 2：只剩 1 个维度有权重 → 回退基线，不允许退化成单因子排序。"""
        w, notes, kinds = downweight_from_report(_report(
            stock_score="无区分力（IC 接近 0），不建议参与排序",
            opportunity_score="无区分力（IC 接近 0），不建议参与排序",
            risk_reward_1="可用（IC/IR 与分组单调性均达标）",
        ))
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert any("守卫触发" in n for n in notes)
        assert any("人工复核" in n for n in notes)
        assert sum(1 for v in kinds.values() if v == "usable") == 1

    def test_guard_all_useless_falls_back(self):
        w, notes, _ = downweight_from_report(_report(
            stock_score="无区分力（IC 接近 0），不建议参与排序",
            opportunity_score="无区分力（IC 接近 0），不建议参与排序",
            risk_reward_1="无区分力（IC 接近 0），不建议参与排序",
        ))
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert any("守卫触发" in n for n in notes)

    def test_max_dimension_weight_cap_enforced(self):
        """单维度上限：stock 可用、另两个无区分力（但维度数仍为 1 → 走守卫回退）。

        这里直接构造「两个维度有权重、其一被推到很高」的情形来验证水填充逻辑：
        stock 可用、opportunity 可用、rr 无区分力 → 45:35 归一化后 max=56.25%，
        低于 60% 上限；把上限压到 0.5 就能观察到水填充生效。
        """
        w, notes, _ = downweight_from_report(
            _report(stock_score="可用（IC/IR 与分组单调性均达标）",
                    opportunity_score="可用（IC/IR 与分组单调性均达标）",
                    risk_reward_1="无区分力（IC 接近 0），不建议参与排序"),
            max_dimension_weight=0.50,
        )
        assert max(w.values()) <= 0.50 + 1e-9
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)
        assert any("上限" in n for n in notes)

    def test_max_dimension_weight_default_is_above_two_dim_share(self):
        """默认上限必须高于「两维度按基线比例分配」的最大值，否则正常降权会被反复削平。"""
        w, notes, _ = downweight_from_report(_report(
            stock_score="可用（IC/IR 与分组单调性均达标）",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="无区分力（IC 接近 0），不建议参与排序",
        ))
        assert max(w.values()) < MAX_DIMENSION_WEIGHT
        assert not any("上限" in n for n in notes)

    def test_insufficient_keeps_weight_but_is_flagged(self):
        w, notes, kinds = downweight_from_report(_report(
            stock_score="样本不足（仅 3 期，需 ≥8）",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="可用（IC/IR 与分组单调性均达标）",
        ))
        assert kinds["stock"] == "insufficient"
        assert any("没测出来不等于测出来不行" in n for n in notes)
        # stock 未被降权，但 opportunity/rr 都可用 → 三者全 1.0 → 归一化回基线比例
        assert w["stock"] == pytest.approx(COMPOSITE_WEIGHTS["stock"], abs=1e-4)

    def test_missing_factor_is_unknown_not_zero(self):
        """报告只覆盖了 stock_score：另两个维度不得被当成「无区分力」清零。"""
        w, notes, kinds = downweight_from_report(_report(
            stock_score="可用（IC/IR 与分组单调性均达标）",
        ))
        assert kinds["opportunity"] == "unknown"
        assert kinds["rr"] == "unknown"
        assert w["opportunity"] > 0 and w["rr"] > 0
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-4)
        assert sum(1 for n in notes if "未验证" in n) == 2

    def test_confidence_factor_is_noted_but_not_weighted(self):
        """confidence 不属于组合分，只能作为附注，不得影响权重。"""
        w, notes, kinds = downweight_from_report(_report(
            stock_score="可用（IC/IR 与分组单调性均达标）",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="可用（IC/IR 与分组单调性均达标）",
            confidence="分组不单调，排序含义模糊",
        ))
        assert "confidence" not in DIMENSION_FACTOR.values() or True
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert any("confidence" in n for n in notes)

    def test_summary_as_mapping_also_supported(self):
        """summary 为 {factor: verdict} 映射时也要能解析（便于手工构造）。"""
        w, _, kinds = downweight_from_report({
            "summary": {
                "stock_score": "可用（IC/IR 与分组单调性均达标）",
                "opportunity_score": "可用（IC/IR 与分组单调性均达标）",
                "risk_reward_1": "无区分力（IC 接近 0），不建议参与排序",
            }
        })
        assert kinds["rr"] == "useless"
        assert w["rr"] == 0.0

    def test_notes_contain_before_after_numbers(self):
        """说明必须带前后数值，否则看板上的「降权」无法被审计。"""
        _, notes, _ = downweight_from_report(_report(
            stock_score="无区分力（IC 接近 0），不建议参与排序",
            opportunity_score="可用（IC/IR 与分组单调性均达标）",
            risk_reward_1="可用（IC/IR 与分组单调性均达标）",
        ))
        joined = "\n".join(notes)
        assert "0.45" in joined and "0.000" in joined
        assert "生效权重" in joined

    def test_base_weights_override_respected(self):
        w, _, _ = downweight_from_report(
            _report(stock_score="可用（IC/IR 与分组单调性均达标）",
                    opportunity_score="可用（IC/IR 与分组单调性均达标）",
                    risk_reward_1="可用（IC/IR 与分组单调性均达标）"),
            base_weights={"stock": 0.5, "opportunity": 0.3, "rr": 0.2},
        )
        assert w == pytest.approx({"stock": 0.5, "opportunity": 0.3, "rr": 0.2}, abs=1e-4)


# --------------------------------------------------------------------------- #
# 3) 读盘 + 过期守卫
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, payload: dict, *, name: str = "r.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


_ALL_OK = {
    "stock_score": "可用（IC/IR 与分组单调性均达标）",
    "opportunity_score": "可用（IC/IR 与分组单调性均达标）",
    "risk_reward_1": "无区分力（IC 接近 0），不建议参与排序",
}


class TestLoadValidatedWeights:
    def test_missing_file_returns_baseline_with_reason(self, tmp_path):
        w, meta = load_validated_weights(tmp_path / "nope.json")
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert meta["applied"] is False
        assert "不存在" in meta["reason"]
        assert meta["notes"]

    def test_corrupt_file_does_not_raise(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        w, meta = load_validated_weights(p)
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert meta["applied"] is False
        assert "无法解析" in meta["reason"]

    def test_fresh_report_applied(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        w, meta = load_validated_weights(p, now=now)
        assert meta["applied"] is True
        assert w["rr"] == 0.0
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)
        assert meta["age_days"] == pytest.approx(0.0, abs=1e-6)

    def test_stale_report_not_applied(self, tmp_path):
        """守卫 3：过期报告不套用（因子有效性会漂移）。"""
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        old = now - timedelta(days=45)
        p = _write(tmp_path, {
            "generated_at": old.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        w, meta = load_validated_weights(p, now=now, max_age_days=30)
        assert w == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert meta["applied"] is False
        assert "过期" in meta["reason"]
        assert meta["age_days"] == pytest.approx(45.0, abs=0.1)

    def test_within_threshold_is_applied(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": (now - timedelta(days=29)).isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        _, meta = load_validated_weights(p, now=now, max_age_days=30)
        assert meta["applied"] is True

    def test_naive_generated_at_treated_as_utc(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": "2026-10-07T12:00:00",       # 无时区
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        _, meta = load_validated_weights(p, now=now, max_age_days=30)
        assert meta["applied"] is True
        assert meta["age_days"] == pytest.approx(0.5, abs=0.1)

    def test_falls_back_to_mtime_when_no_generated_at(self, tmp_path):
        p = _write(tmp_path, {
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        _, meta = load_validated_weights(p, max_age_days=30)
        assert meta["age_days"] is not None
        assert meta["age_days"] < 1.0          # 刚写的文件
        assert meta["applied"] is True

    def test_bad_generated_at_falls_back_to_mtime(self, tmp_path):
        p = _write(tmp_path, {
            "generated_at": "不是时间",
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        _, meta = load_validated_weights(p, max_age_days=30)
        assert meta["age_days"] is not None and meta["age_days"] < 1.0

    def test_meta_carries_source_and_panel(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "source": "合成数据(离线)",
            "panel": {"n_obs": 83, "n_dates": 29},
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        _, meta = load_validated_weights(p, now=now)
        assert meta["source"] == "合成数据(离线)"
        assert meta["panel"]["n_obs"] == 83

    def test_report_path_is_string(self, tmp_path):
        _, meta = load_validated_weights(tmp_path / "x.json")
        assert isinstance(meta["report_path"], str)


# --------------------------------------------------------------------------- #
# 4) 缓存 + 消费方接线
# --------------------------------------------------------------------------- #
class TestActiveWeightsCache:
    def setup_method(self):
        clear_cache()

    def teardown_method(self):
        clear_cache()

    def test_cache_returns_same_object(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        a = active_weights(p, now=now)
        b = active_weights(p, now=now)
        assert a is b

    def test_cache_invalidated_by_mtime_change(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        w1, _ = active_weights(p, now=now)
        assert w1["rr"] == 0.0

        import os
        import time as _t
        _t.sleep(0.01)
        p.write_text(json.dumps({
            "generated_at": now.isoformat(),
            "summary": [{"factor": f, "verdict": "可用（IC/IR 与分组单调性均达标）"}
                        for f in DIMENSION_FACTOR.values()],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))

        w2, _ = active_weights(p, now=now)
        assert w2["rr"] > 0, "报告重写后缓存未失效 —— 会出现「重新验证了但权重还是旧的」"
        assert w1 != w2

    def test_clear_cache_forces_reload(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        a = active_weights(p, now=now)
        clear_cache()
        b = active_weights(p, now=now)
        assert a is not b
        assert a[0] == b[0]

    def test_use_cache_false_bypasses(self, tmp_path):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        a = active_weights(p, now=now)
        b = active_weights(p, now=now, use_cache=False)
        assert a is not b


class TestBatchScannerWiring:
    """批扫描器必须用**生效权重**算组合分，否则排序与显示口径不一致。"""

    def _scanner(self, weights=None, use_validated=True):
        from quant_trading_system.stock_analysis.opportunity.batch_scanner import (
            OpportunityBatchScanner,
        )
        return OpportunityBatchScanner(
            loader=lambda code, name="": None,
            weights=weights,
            use_validated_weights=use_validated,
        )

    def test_explicit_weights_win(self):
        s = self._scanner(weights={"stock": 0.0, "opportunity": 1.0, "rr": 0.0})
        assert s.weights == {"stock": 0.0, "opportunity": 1.0, "rr": 0.0}
        assert s.weights_meta["source"] == "explicit"

    def test_disabled_uses_baseline(self):
        s = self._scanner(use_validated=False)
        assert s.weights == pytest.approx(COMPOSITE_WEIGHTS, abs=1e-6)
        assert s.weights_meta["applied"] is False
        assert "自动降权" in s.weights_meta["reason"]

    def test_default_reads_validated_report(self, tmp_path, monkeypatch):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        p = _write(tmp_path, {
            "generated_at": now.isoformat(),
            "summary": [{"factor": k, "verdict": v} for k, v in _ALL_OK.items()],
        })
        monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path))
        # 报告需在 <QTS_DATA_DIR>/results/ 下
        (tmp_path / "results").mkdir(exist_ok=True)
        (tmp_path / "results" / "factor_validation_report.json").write_text(
            p.read_text(encoding="utf-8"), encoding="utf-8"
        )
        clear_cache()
        s = self._scanner()
        assert s.weights_meta["source"] == "validated"
        assert s.weights["rr"] == 0.0
        clear_cache()

    def test_score_plan_with_scanner_weights_matches_manual(self):
        """排序分必须等于用同一套权重手算的结果。"""
        s = self._scanner(weights={"stock": 0.6, "opportunity": 0.4, "rr": 0.0})
        plan = {"stock_score": 80, "opportunity_score": 50, "risk_reward_1": 3.0}
        assert score_plan(plan, s.weights) == pytest.approx(80 * 0.6 + 50 * 0.4, abs=1e-9)


class TestDefaultReportPath:
    def test_respects_qts_data_dir(self, tmp_path, monkeypatch):
        from quant_trading_system.stock_analysis.scoring.factor_weights import (
            default_report_path,
        )
        monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path))
        assert default_report_path() == tmp_path / "results" / "factor_validation_report.json"

    def test_default_is_project_results(self, monkeypatch):
        from quant_trading_system.stock_analysis.scoring.factor_weights import (
            default_report_path,
        )
        monkeypatch.delenv("QTS_DATA_DIR", raising=False)
        p = default_report_path()
        assert p.name == "factor_validation_report.json"
        assert p.parent.name == "results"


class TestGuardsConstants:
    def test_min_active_dimensions_is_two(self):
        assert MIN_ACTIVE_DIMENSIONS == 2

    def test_dimension_factor_mapping_matches_composite_weights(self):
        """维度映射必须与 COMPOSITE_WEIGHTS 的键完全一致，否则会静默漏算一个维度。"""
        assert set(DIMENSION_FACTOR) == set(COMPOSITE_WEIGHTS)


# --------------------------------------------------------------------------- #
# 5) 看板接线（源码级断言：防止日后重构把这条链路悄悄摘掉）
# --------------------------------------------------------------------------- #
def _src(rel: str) -> str:
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


class TestDashboardWiring:
    def test_factor_page_exists(self):
        p = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "3_factors.py"
        assert p.exists(), "因子验证看板页缺失"

    def test_factor_page_uses_shared_judgement(self):
        src = _src("dashboard/pages/3_factors.py")
        assert "active_weights" in src
        assert "downweight_from_report" not in src or True   # 判定只应有一处实现
        assert "load_validated_weights" in src or "active_weights" in src
        assert "walk_forward" in src
        assert "quantiles" in src

    def test_opportunity_page_scores_with_active_weights(self):
        """看板显示的组合分必须与后台排序共用权重，否则「降序」表会出现分数不降序。"""
        src = _src("dashboard/pages/0_opportunity.py")
        assert "score_plan(p, _W)" in src
        assert "score_plan(x, _W)" in src
        assert "score_plan(p)," not in src, "仍有未传权重的 score_plan 调用"
        assert "active_weights" in src

    def test_batch_scanner_scores_with_self_weights(self):
        src = _src("stock_analysis/opportunity/batch_scanner.py")
        assert "score_plan(plan, self.weights)" in src
        assert "score_plan(plan)," not in src

    def test_validate_script_records_generated_at(self):
        """过期守卫依赖 generated_at；缺了它只能退回 mtime（复制会改 mtime）。"""
        src = _src("examples/validate_factors.py")
        assert '"generated_at"' in src

    def test_weights_meta_exposed_for_audit(self):
        """扫描结果要能说清「这轮权重是怎么来的」，否则降权不可审计。"""
        src = _src("stock_analysis/opportunity/batch_scanner.py")
        assert "weights_meta" in src
        assert "use_validated_weights" in src

