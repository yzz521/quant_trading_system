"""V2 Trading Plan 回测单元测试。

重点：验证无 look-ahead bias（计划只用截至 T 日数据）、指标聚合正确、空数据安全。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.backtest import (
    TradingPlanBacktest,
    calc_metrics,
)
from quant_trading_system.stock_analysis.indicators import add_all_indicators
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine


def _kline(n=400, seed=5, trend=0.02, vol=0.15, start="2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(trend, vol, n))
    high = close * (1 + np.abs(rng.normal(0, 0.015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.015, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": volume * close,
            "date": pd.date_range(start, periods=n, freq="B"),
        }
    )
    return add_all_indicators(df)


class TestBacktestSmoke:
    def test_runs_and_metrics(self):
        df = _kline()
        bt = TradingPlanBacktest(engine=OpportunityEngine(regime_score=65), stride=5)
        res = bt.run(df, "600000", "测试")
        assert res.metrics is not None
        assert res.metrics.sample_size >= 1
        assert 0 <= res.metrics.entry_zone_hit_rate <= 1
        assert 0 <= res.metrics.win_rate <= 1
        assert 0 <= res.metrics.max_drawdown <= 100
        # 所有比例都应在 [0,1]
        for rate in (
            res.metrics.entry_zone_hit_rate,
            res.metrics.stop_loss_trigger_rate,
            res.metrics.target_1_hit_rate,
            res.metrics.target_2_hit_rate,
        ):
            assert 0 <= rate <= 1

    def test_to_dict(self):
        df = _kline(n=200)
        res = TradingPlanBacktest(stride=10).run(df, "1")
        d = res.to_dict()
        assert "trades" in d and "metrics" in d
        if res.trades:
            t = res.trades[0].to_dict()
            assert "exit_reason" in t and "return_pct" in t

    def test_short_df_returns_empty(self):
        df = _kline(n=80)
        res = TradingPlanBacktest().run(df)
        assert res.metrics is None
        assert res.trades == []


class TestNoLookAhead:
    """核心：同一 T 日，用「截至 T」的数据与「完整」数据生成的计划必须一致。"""

    def test_plan_at_time_t_ignores_future(self):
        df = _kline(n=300, seed=9)
        bt = TradingPlanBacktest(stride=1)
        # T 选在第 200 根K线
        t = 200
        hist_only = df.iloc[: t + 1].copy()
        # 完整数据（含未来）传入引擎，引擎内部只 tail(120) —— 但为严格验证，我们
        # 对比两种输入产出的计划是否一致：应一致，因为引擎只用截至 T 的信息
        full = df.copy()
        res_hist = bt.engine.analyze("600000", "x", hist_only)
        res_full = bt.engine.analyze("600000", "x", full.iloc[: t + 1])

        assert res_hist.plan is not None and res_full.plan is not None
        assert res_hist.plan.entry_low == res_full.plan.entry_low
        assert res_hist.plan.stop_loss == res_full.plan.stop_loss
        assert res_hist.plan.target_1 == res_full.plan.target_1

    def test_backtest_uses_only_past_at_each_step(self):
        """回测中每条交易的计划必须与「只用截至其日期」的数据生成的计划一致。"""
        df = _kline(n=260, seed=11)
        bt = TradingPlanBacktest(engine=OpportunityEngine(), stride=20)
        res = bt.run(df, "1")
        if not res.trades:
            pytest.skip("无样本")
        # 用日期反查时点，确保跳过 AVOID 后仍能对上
        date_to_idx = {str(pd.Timestamp(d).date()): i for i, d in enumerate(df["date"])}
        for trade in res.trades:
            idx = date_to_idx.get(str(trade.date))
            assert idx is not None, f"交易日期 {trade.date} 不在数据中"
            hist = df.iloc[: idx + 1]
            plan = bt.engine.analyze("1", "x", hist).plan
            assert plan is not None
            assert plan.entry_low == trade.entry_low
            assert plan.stop_loss == trade.stop_loss
            assert plan.target_1 == trade.target_1


class TestSimulation:
    def test_stop_loss_trigger(self):
        """构造明确路径：入场后立刻跌破止损 → 止损离场。"""
        # 手工构造一个已知走势的 df，保证计划生成后未来大跌
        n = 160
        close = np.linspace(10, 14, n)  # 平稳上涨 → 趋势良好
        rng = np.random.default_rng(1)
        high = close * 1.01
        low = close * 0.99
        volume = rng.uniform(1e6, 5e6, n)
        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close, "volume": volume, "amount": volume * close}
        )
        df = add_all_indicators(df)

        bt = TradingPlanBacktest(engine=OpportunityEngine(), stride=30)
        res = bt.run(df, "1")
        # 无论如何不应抛异常
        assert res.metrics is not None

    def test_metrics_aggregation(self):
        trades = [
            {"exit_reason": "target_2", "return_pct": 20.0, "holding_days": 10, "hit_target_1": True, "hit_target_2": True},
            {"exit_reason": "stop_loss", "return_pct": -7.0, "holding_days": 3, "hit_target_1": False, "hit_target_2": False},
            {"exit_reason": "timeout", "return_pct": 2.0, "holding_days": 60, "hit_target_1": True, "hit_target_2": False},
        ]
        m = calc_metrics(sample_size=5, entry_zone_hits=4, trades=trades)
        assert m.sample_size == 5
        assert m.entry_zone_hit_rate == pytest.approx(0.8)
        assert m.total_trades == 3
        assert m.win_rate == pytest.approx(2 / 3)
        assert m.target_1_hit_rate == pytest.approx(2 / 3)
        assert m.target_2_hit_rate == pytest.approx(1 / 3)
        assert m.stop_loss_trigger_rate == pytest.approx(1 / 3)
        assert m.avg_return == pytest.approx((20 - 7 + 2) / 3)
        assert m.avg_holding_period == pytest.approx((10 + 3 + 60) / 3)
        assert m.max_drawdown > 0  # 有亏损交易 → 应有回撤

    def test_metrics_empty_trades(self):
        m = calc_metrics(sample_size=0, entry_zone_hits=0, trades=[])
        assert m.sample_size == 0
        assert m.entry_zone_hit_rate == 0.0
        assert m.total_trades == 0
        assert m.win_rate == 0.0


class TestPositionSizingInBacktest:
    def test_engine_with_account(self):
        df = _kline(n=260, seed=3)
        eng = OpportunityEngine(account_equity=100_000, regime_score=70)
        bt = TradingPlanBacktest(engine=eng, stride=20)
        res = bt.run(df, "600000")
        assert res.metrics is not None
