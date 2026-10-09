"""V2 opportunity 模块单元测试（纯离线，不联网）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import (
    OpportunityEngine,
    SupportResistance,
    build_trading_plan,
    calc_entry_zone,
    calc_exit_prices,
    calc_position_size,
    calc_risk_reward,
    detect_support_resistance,
    reconcile_entry_zone,
)
from quant_trading_system.stock_analysis.opportunity.entry_price import (
    MAX_ENTRY_ABOVE,
    MAX_ENTRY_HALF_WIDTH,
)
from quant_trading_system.stock_analysis.opportunity.exit_price import (
    MAX_T1_ATR_MULT,
    MIN_STOP_ATR_MULT,
)
from quant_trading_system.stock_analysis.opportunity.trading_plan import DecisionState


def _kline(n=160, seed=42, trend=0.03, vol=0.12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
    )
    return add_all_indicators(df)


def _kline_crash(n=160, seed=7) -> pd.DataFrame:
    """前 119 日都在 9.8 元上方，**最后一日向下破位到 8.0**。

    这样「近 120 日最低价（不含今日）」= ~9.7 **高于现价 8.0** ——
    用来钉住 ``calc_entry_zone`` 里 ``prev_low`` 候选漏掉的 ``< cur`` 过滤。
    """
    rng = np.random.default_rng(seed)
    close = np.maximum(10.0 + rng.normal(0, 0.05, n), 9.8)
    close[-1] = 8.0
    high = close * 1.01
    low = close * 0.99
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume,
         "amount": volume * close}
    )
    return add_all_indicators(df)


def _kline_far_high(n=160, seed=11) -> pd.DataFrame:
    """先在 30 元附近，再长期下跌到 10 元附近 —— 让「近 120 日最高价」远高于现价。"""
    rng = np.random.default_rng(seed)
    close = np.concatenate([
        np.full(40, 30.0) + rng.normal(0, 0.3, 40),
        np.linspace(29.0, 10.0, n - 40),
    ])
    high = close * 1.01
    low = close * 0.99
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume,
         "amount": volume * close}
    )
    return add_all_indicators(df)


class TestSupportResistance:
    def test_detects_levels(self):
        df = _kline()
        sr = detect_support_resistance(df)
        assert len(df) >= 30
        assert sr.supports  # 至少有一个支撑
        if sr.key_support is not None:
            assert sr.key_support < float(df["close"].iloc[-1]) * 1.02

    def test_empty_df_safe(self):
        sr = detect_support_resistance(pd.DataFrame())
        assert sr.supports == [] and sr.key_support is None

    def test_to_dict(self):
        sr = detect_support_resistance(_kline())
        d = sr.to_dict()
        assert set(d) == {"supports", "resistances", "key_support", "key_resistance", "evidence"}


class TestEntryPrice:
    def test_entry_zone_ordering(self):
        df = _kline()
        entry = calc_entry_zone(df)
        assert entry.ideal is not None and entry.standard is not None and entry.aggressive is not None
        assert entry.ideal <= entry.standard <= entry.aggressive
        assert entry.low <= entry.high

    def test_zone_is_anchored_on_standard(self):
        """2026-10 裁决：入场带以「标准入场价」为中心，宽度受限。

        原实现取 ``low = min(ideal, standard)``、``high = aggressive``，
        ``ideal`` 可落到 120 日最低点、``aggressive`` 可落到 120 日最高点
        → 实测区间中位宽 18.6%、最大 160.5%，并导致 57.5% 的计划出现
        ``entry_low < stop_loss``。现在区间必须含 standard 且半宽 ≤ 3%。
        """
        entry = calc_entry_zone(_kline())
        assert entry.low <= entry.standard <= entry.high
        half = (entry.high - entry.low) / 2
        assert half <= entry.standard * MAX_ENTRY_HALF_WIDTH + 0.011
        assert (entry.high - entry.standard) == pytest.approx(
            entry.standard - entry.low, abs=0.011
        )

    def test_deep_ideal_does_not_widen_zone(self):
        """``ideal`` 远低于 standard 时只作提示，不撑开下沿。"""
        entry = calc_entry_zone(_kline())
        if entry.ideal < entry.low:
            assert "deep_pullback_hint" in entry.evidence
            assert entry.evidence["deep_pullback_hint"]["price"] == entry.ideal

    def test_aggressive_capped_near_price(self):
        """激进入场（可能是 120 日前高）不得离现价超过 2%，超出部分降级为提示。"""
        df = _kline()
        cur = float(df["close"].iloc[-1])
        entry = calc_entry_zone(df)
        assert entry.aggressive <= cur * (1 + MAX_ENTRY_ABOVE) + 1e-9
        if "breakout_hint" in entry.evidence:
            assert entry.evidence["breakout_hint"]["price"] > entry.aggressive

    def test_prev_low_above_price_is_not_used_as_standard(self):
        """破位日：近 120 日最低价**高于**现价，不得当作「标准入场价」。

        回归：``bases`` 里其它候选都带 ``< cur`` 过滤，唯独 ``prev_low`` 漏了。
        于是向下破位时「标准入场价」会跑到现价上方（实测华工科技：现价 86.99、
        标准入场 90.23），止损/目标/赔率全部算在一个买不到的价格上。
        """
        df = _kline_crash()
        cur = float(df["close"].iloc[-1])
        prev_low = float(df["low"].iloc[:-1].min())
        assert prev_low > cur              # 前提：构造确实造成了「前低高于现价」
        entry = calc_entry_zone(df)
        assert entry.standard < cur
        assert entry.low < cur

    def test_empty_safe(self):
        e = calc_entry_zone(pd.DataFrame())
        assert e.standard is None


class TestReconcileEntryZone:
    """入场区间与止损/目标的自洽性夹逼（2026-10 裁决 4b）。"""

    def test_low_lifted_above_stop(self):
        low, high, ok, note = reconcile_entry_zone(
            3.98, 5.72, stop_loss=5.21, target_1=5.68
        )
        assert ok is True
        assert low > 5.21
        assert low <= 5.72
        assert note and "夹逼" in note

    def test_high_capped_below_target(self):
        low, high, ok, note = reconcile_entry_zone(
            4.58, 6.50, stop_loss=4.61, target_1=4.77
        )
        assert ok is True
        assert high < 4.77

    def test_empty_zone_is_not_ok(self):
        # 止损与目标贴在一起 → 没有可下单空间
        low, high, ok, note = reconcile_entry_zone(
            9.0, 11.0, stop_loss=10.0, target_1=10.02
        )
        assert ok is False
        assert low == 9.0 and high == 11.0   # 原样返回
        assert "无法自洽" in note

    def test_stop_above_target_is_not_ok(self):
        _, _, ok, note = reconcile_entry_zone(
            9.0, 11.0, stop_loss=11.0, target_1=10.0
        )
        assert ok is False
        assert "计划无效" in note

    def test_missing_risk_params_passes_through(self):
        low, high, ok, note = reconcile_entry_zone(9.0, 11.0, stop_loss=None, target_1=12.0)
        assert (low, high, ok, note) == (9.0, 11.0, True, "")


class TestExitPrice:
    def test_stop_and_targets(self):
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        assert ex.stop_loss is not None and ex.stop_loss < entry.standard
        assert ex.target_1 is not None and ex.target_1 > entry.standard
        assert ex.target_1 < ex.target_2 < ex.target_3
        assert ex.expected_return is not None and ex.risk_reward is not None

    def test_empty_safe(self):
        ex = calc_exit_prices(pd.DataFrame())
        assert ex.stop_loss is None

    # ----------------------------------------------------------------- #
    # 止损距离的 ATR 下限（2026-10 裁决 4d）
    #
    # 原实现 `stop = max(candidates)` 取「最接近现价」= **最紧**的一档。
    # 这看起来最保守（每股亏损最小），实际把止损塞进日内噪声 —— 一天的平均
    # 波动就能扫掉它，而赔率因为分母被压小反而显得漂亮。
    # ----------------------------------------------------------------- #
    def test_stop_distance_at_least_half_atr(self):
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        atr = float(df["atr"].iloc[-1])
        assert entry.standard - ex.stop_loss >= MIN_STOP_ATR_MULT * atr - 0.011

    def test_atr_floor_never_widens_beyond_fixed_risk(self):
        """ATR 很大时也不允许把风险撑过固定风险比例（7%）。"""
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        assert (entry.standard - ex.stop_loss) / entry.standard <= 0.07 + 1e-6

    def test_atr_floor_can_be_disabled(self):
        df = _kline()
        entry = calc_entry_zone(df)
        loose = calc_exit_prices(df, entry_price=entry.standard, min_stop_atr_mult=0.0)
        tight = calc_exit_prices(df, entry_price=entry.standard)
        assert loose.stop_loss >= tight.stop_loss

    # ----------------------------------------------------------------- #
    # T1 的波动率上限（2026-10 裁决 4c）
    #
    # `t1` 的候选里有 `prev_high`（近 120 日最高价）。对一只已从高位跌下来的
    # 股票它远在现价之上：实测华工科技 T1=+108%（≈18×ATR）、立讯精密 +66.8%
    # （≈16×ATR），RR 因此变成 15.42 / 10.49，白过 min_rr=2.0 的闸门。
    # ----------------------------------------------------------------- #
    def test_t1_capped_by_atr_multiple(self):
        df = _kline_far_high()
        entry = calc_entry_zone(df)
        # 清空关键阻力，强制走「前高」分支
        sr = SupportResistance(key_resistance=None)
        ex = calc_exit_prices(df, entry_price=entry.standard, sr=sr)
        atr = float(df["atr"].iloc[-1])
        assert ex.target_1 <= entry.standard + MAX_T1_ATR_MULT * atr + 0.011
        assert "ATR 上限约束" in ex.evidence["t1_source"]

    def test_t1_cap_can_be_disabled(self):
        df = _kline_far_high()
        entry = calc_entry_zone(df)
        sr = SupportResistance(key_resistance=None)
        loose = calc_exit_prices(df, entry_price=entry.standard, sr=sr,
                                 max_t1_atr_mult=0.0)
        tight = calc_exit_prices(df, entry_price=entry.standard, sr=sr)
        assert loose.target_1 >= tight.target_1

    def test_t1_still_above_entry_after_cap(self):
        df = _kline_far_high()
        entry = calc_entry_zone(df)
        sr = SupportResistance(key_resistance=None)
        ex = calc_exit_prices(df, entry_price=entry.standard, sr=sr)
        assert ex.target_1 > entry.standard


class TestRiskReward:
    def test_calculation(self):
        rr = calc_risk_reward(11.95, 11.35, 13.20, 14.50)
        assert rr.risk == pytest.approx(0.60)
        assert rr.reward_1 == pytest.approx(1.25)
        assert rr.ratio_1 == pytest.approx(2.08, abs=0.01)
        assert rr.ratio_2 == pytest.approx(4.25, abs=0.01)
        assert rr.grade == "良好"

    def test_low_ratio_grade(self):
        rr = calc_risk_reward(11.95, 11.35, 12.30, 13.00)
        assert rr.grade == "不推荐"

    def test_invalid_input(self):
        rr = calc_risk_reward(0, 0, 0, 0)
        assert rr.ratio_1 is None
        assert rr.tradable is False          # 参数不全 → 不可评估赔率

    # ----------------------------------------------------------------- #
    # 止损距离下限（2026-10 裁决）
    #
    # 原实现只判 `risk <= 0`。于是「止损贴着入场价」的计划畅通无阻，而它的
    # RR 会被数学放大成几十上百倍（实测止损距离 0.01% → risk_reward_1=180）。
    # 这类计划不是高赔率机会：止损距离比交易成本还近 = 开仓即亏，
    # 且历史样本里高 RR 组 97.1% 止损离场。
    # ----------------------------------------------------------------- #
    def test_rejects_stop_hugging_entry(self):
        entry = 100.0
        rr = calc_risk_reward(entry, entry * 0.9999, 110.0, 120.0)   # 止损距离 0.01%
        assert rr.tradable is False
        assert rr.ratio_1 is None
        assert rr.ratio_2 is None
        assert "止损距离" in rr.note

    def test_accepts_stop_far_enough(self):
        rr = calc_risk_reward(100.0, 97.0, 106.0, 110.0)             # 止损距离 3%
        assert rr.tradable is True
        assert rr.ratio_1 == pytest.approx(2.0, abs=0.01)

    def test_boundary_just_above_threshold_is_tradable(self):
        # 阈值 1.0%。注意别用 100/99 做「恰好 1%」——浮点下 100-99 = 1.0 但
        # 经 /100 后可能落在阈值之下。这里取 1.01%，明确在阈值之上。
        rr = calc_risk_reward(100.0, 98.99, 110.0, 120.0)
        assert rr.tradable is True

    def test_boundary_just_below_threshold_is_not_tradable(self):
        rr = calc_risk_reward(100.0, 99.01, 110.0, 120.0)   # 0.99%
        assert rr.tradable is False

    def test_threshold_covers_round_trip_cost(self):
        """阈值必须高于双边交易成本 —— 否则「止损距离比成本还近」的荒谬计划会被放行。

        成本模型（``backtest.execution.CostModel``）：买 0.076% + 卖 0.126% ≈ 0.20%
        （名义成本，不触最低佣金）。历史实现取 0.3%，与注释里自己引用的
        「0.1%~0.2%」成本线只差一线，几乎等于没设。
        """
        from quant_trading_system.stock_analysis.backtest.execution import CostModel
        from quant_trading_system.stock_analysis.opportunity.risk_reward import MIN_RISK_PCT

        cost = CostModel()
        base = cost.round_trip_cost_pct(100.0, 100.0)   # 单位：百分比
        assert base < 0.3
        assert MIN_RISK_PCT * 100 > 2 * base            # 至少 2 倍名义成本
        # 0.31% 的止损距离（旧阈值 0.3% 下会被放行）现在必须被拒
        rr = calc_risk_reward(100.0, 99.69, 110.0, 120.0)
        assert rr.tradable is False
        # 记录一个单独的问题：小额订单（¥1000）因最低佣金（5 元/笔），双边成本
        # 可达 1.15%，**高于本阈值**。这是仓位规模问题（应设最小下单金额或
        # 提示「单笔金额过小」），不是把止损下限一路抬高能解决的。
        assert cost.round_trip_cost_pct(100.0, 100.0, notional=1_000.0) > 1.0

    def test_min_risk_pct_is_configurable(self):
        """阈值可调（研究/对照场景可显式关闭），但默认必须生效。"""
        rr = calc_risk_reward(100.0, 99.9, 110.0, 120.0, min_risk_pct=0.0)
        assert rr.tradable is True
        assert rr.ratio_1 is not None

    def test_stop_above_entry_is_invalid(self):
        rr = calc_risk_reward(100.0, 101.0, 110.0, 120.0)
        assert rr.tradable is False
        assert rr.ratio_1 is None
        assert rr.note


class TestPositionSizing:
    def test_position_bounds(self):
        ps = calc_position_size(100_000, 11.95, 11.35)
        # 单笔风险 2% = 2000 元；每股风险 0.6 → 最多 3300 股（整手）
        assert ps.max_shares == 3300
        assert ps.suggested_shares <= 3300
        assert ps.position_amount <= 100_000 * 0.20
        assert ps.position_percent <= 20.0

    def test_cap_limits(self):
        # 资金大、风险小 → 触发单票 20% 上限
        ps = calc_position_size(1_000_000, 11.95, 11.80)
        assert ps.capped is True
        assert ps.suggested_shares * 11.95 <= 1_000_000 * 0.20 + 1195

    def test_zero_equity(self):
        ps = calc_position_size(0, 11.95, 11.35)
        assert ps.suggested_shares is None

    # ---- 回归：两个上限都必须真正生效（原实现在任一为 0 时取 max） ----
    def test_zero_risk_budget_does_not_buy_up_to_cap(self):
        """风险预算连一手都买不起 → 必须 0 股，而不是「买满单票上限」。

        夹具：账户 1000 元，入场 100 元、止损 50 元 → 每股风险 50 元，
        单笔风险 2% = 20 元 → 买不起一手（100 股 × 100 元 = 10000 元）。
        原实现会返回 cap_shares（= 0，因 1000×20% 也买不起一手）……
        所以这里再用「止损极近」的另一种构造验证另一个方向。
        """
        # 风险预算 0.2% × 1000 = 2 元，每股风险 50 元 → max_shares = 0
        ps = calc_position_size(1000, 100.0, 50.0, risk_percent=0.002,
                                max_position_pct=5.0)
        assert ps.max_shares == 0
        assert ps.suggested_shares == 0
        assert ps.position_amount == 0.0

    def test_cap_zero_does_not_exceed_single_name_limit(self):
        """一手就超过单票上限 → 必须 0 股，而不是按风险预算满买。"""
        # 账户 1000 元、单票上限 20% = 200 元；股价 500 元/股，一手 5 万元
        ps = calc_position_size(1000, 500.0, 400.0, max_position_pct=0.20)
        assert ps.suggested_shares == 0
        assert ps.position_amount == 0.0

    def test_never_exceeds_either_bound(self):
        """随机抽样：建议股数必须同时 ≤ 风险预算上限与单票上限。"""
        import numpy as np

        rng = np.random.default_rng(7)
        for _ in range(200):
            eq = float(rng.uniform(1e3, 1e7))
            entry = float(rng.uniform(1.0, 2000.0))
            stop = entry * float(rng.uniform(0.3, 0.99))
            ps = calc_position_size(eq, entry, stop)
            if ps.suggested_shares is None:
                continue
            assert ps.suggested_shares <= ps.max_shares
            assert ps.suggested_shares * entry <= eq * 0.20 + 1e-6
            assert ps.suggested_shares >= 0


class TestTradingPlan:
    def test_build_and_decision_avoid(self):
        df = _kline()
        entry = calc_entry_zone(df)
        ex = calc_exit_prices(df, entry_price=entry.standard)
        rr = calc_risk_reward(entry.standard, ex.stop_loss, ex.target_1, ex.target_2)
        plan = build_trading_plan(
            code="600000", name="浦发银行", current_price=float(df["close"].iloc[-1]),
            entry=entry, exit_=ex, rr=rr, stock_score=70.0, opportunity_score=60.0,
            position_percent=10.0, confidence=0.8,
        )
        assert plan.code == "600000"
        assert plan.decision in DecisionState
        assert plan.entry_low == entry.low
        assert plan.target_1 == ex.target_1
        d = plan.to_dict()
        assert d["decision_emoji"] in ("🟢", "🟡", "🟠", "🔴", "⛔")

    def test_buy_now_when_price_in_zone(self):
        # 构造现价已落入入场区间 → BUY_NOW
        entry = type("E", (), {"low": 10.0, "high": 12.0, "ideal": 10.5, "standard": 11.0})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 14.0, "target_2": 16.0, "target_3": 18.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 2.5, "ratio_2": 4.0, "grade": "良好"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.0, entry=entry, exit_=ex, rr=rr,
            stock_score=80, opportunity_score=80,
        )
        assert plan.decision == DecisionState.BUY_NOW

    def test_buy_on_pullback_when_elevated(self):
        entry = type("E", (), {"low": 10.0, "high": 10.5, "ideal": 10.2, "standard": 10.3})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 13.0, "target_2": 15.0, "target_3": 17.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 2.8, "ratio_2": 5.0, "grade": "良好"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.3, entry=entry, exit_=ex, rr=rr,
            stock_score=80, opportunity_score=80,
        )
        assert plan.decision == DecisionState.BUY_ON_PULLBACK

    # ---- 几何自洽性（2026-10 裁决 4b）----
    def test_geometry_failure_forces_avoid(self):
        """区间与止损/目标矛盾的计划必须直接作废，不给它靠分数/赔率混过闸门。"""
        entry = type("E", (), {"low": 9.0, "high": 12.0, "ideal": 9.5, "standard": 11.0})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 14.0, "target_2": 16.0,
                            "target_3": 18.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 4.0, "ratio_2": 6.0, "grade": "优秀"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.0, entry=entry, exit_=ex, rr=rr,
            stock_score=90, opportunity_score=90,
            geometry_ok=False, geometry_note="入场区间 9.0~12.0 与止损 9.5 无法自洽",
        )
        assert plan.decision == DecisionState.AVOID
        assert plan.meta["geometry_ok"] is False
        assert any("几何不自洽" in r for r in plan.risks)

    def test_geometry_ok_keeps_buy_now(self):
        entry = type("E", (), {"low": 10.0, "high": 12.0, "ideal": 10.5, "standard": 11.0})()
        ex = type("X", (), {"stop_loss": 9.5, "target_1": 14.0, "target_2": 16.0,
                            "target_3": 18.0, "stop_source": "t"})()
        rr = type("R", (), {"ratio_1": 2.5, "ratio_2": 4.0, "grade": "良好"})()
        plan = build_trading_plan(
            code="1", name="t", current_price=11.0, entry=entry, exit_=ex, rr=rr,
            stock_score=80, opportunity_score=80, geometry_ok=True,
        )
        assert plan.decision == DecisionState.BUY_NOW
        assert "geometry_ok" not in plan.meta


class TestOpportunityEngine:
    def test_full_analysis(self):
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze("600000", "测试", df, extra={"total_cap_yi": 120, "pe": 25, "turnover": 2.0})
        assert res.plan is not None
        assert res.sr is not None and res.entry is not None and res.exit_ is not None
        assert res.rr is not None and res.stock_score is not None and res.opportunity_score is not None
        assert res.plan.current_price is not None
        assert res.plan.invalidate_condition  # 应有失效条件
        d = res.to_dict()
        assert d["plan"]["code"] == "600000"
        assert "technical" in res.plan.meta
        assert res.plan.meta["technical"]["grade"] in ("S", "A", "B", "C")
        assert 0 <= res.opportunity_score.components["similar_pattern"] <= 100
        if res.sr and res.sr.evidence:
            assert "fibonacci" in res.sr.evidence

    def test_short_df_returns_empty(self):
        df = _kline(n=20)
        res = OpportunityEngine().analyze("1", "t", df)
        assert res.plan is None

    # ---- 几何不变量（2026-10 裁决 4b）----
    def test_plan_geometry_is_self_consistent(self):
        """端到端不变量：区间必须落在「止损之上、目标之下」。"""
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze(
            "600000", "测试", df,
            extra={"total_cap_yi": 120, "pe": 25, "turnover": 2.0},
        )
        p = res.plan
        assert p is not None
        assert p.entry_low <= p.entry_high
        assert p.entry_low > p.stop_loss
        assert p.entry_high < p.target_1

    def test_risk_and_entry_evidence_are_exposed(self):
        """止损候选/ATR 与入场带来源写进 meta —— 否则只能靠翻代码推断取值理由。"""
        df = _kline()
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        res = eng.analyze(
            "600000", "测试", df,
            extra={"total_cap_yi": 120, "pe": 25, "turnover": 2.0},
        )
        meta = res.plan.meta
        assert "stop_evidence" in meta
        assert "atr" in meta["stop_evidence"]
        assert "stop_candidates" in meta["stop_evidence"]
        assert "entry_evidence" in meta
        assert "zone" in meta["entry_evidence"]
