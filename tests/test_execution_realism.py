"""回测成交真实性测试（P0）。

覆盖 ``backtest/execution.py`` 与 ``TradingPlanBacktest._simulate`` 的成交口径：
限价可达性、交易成本、跳空穿透、涨跌停封板、停牌、容量约束、信号重叠与组合净值。

这些都是**确定性夹具**（手工构造 OHLCV），不依赖网络，也不依赖随机行情——
原先的问题正是「随机行情 + 乐观成交」把 bug 掩盖了。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.backtest import (
    CostModel,
    ExecutionConfig,
    TradingPlanBacktest,
    calc_metrics,
    cost_model_for_market,
    is_suspended,
    limit_pct_for_code,
    price_limit_state,
    simulate_portfolio,
)
from quant_trading_system.stock_analysis.backtest.trading_plan_backtest import BacktestTrade


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _future(rows: list[tuple[float, float, float, float]], *, volume: float = 1e6) -> pd.DataFrame:
    """按 (open, high, low, close) 列表构造未来行情。"""
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [volume] * len(rows),
            "amount": [volume * r[3] for r in rows],
        }
    )


def _trade(**kw) -> BacktestTrade:
    base = dict(
        date="2024-01-02", entry_low=9.80, entry_price=10.00, entry_high=10.50,
        stop_loss=9.20, target_1=11.00, target_2=12.00,
    )
    base.update(kw)
    return BacktestTrade(**base)


def _bt(**kw) -> TradingPlanBacktest:
    kw.setdefault("exec_config", ExecutionConfig())
    return TradingPlanBacktest(**kw)


def _sim(trade: BacktestTrade, future: pd.DataFrame, *, bt=None, prev_closes=None, base_bar=0):
    bt = bt or _bt()
    ctx = {
        "config": bt.exec_config,
        "limit_pct": 0.10,
        "prev_closes": prev_closes if prev_closes is not None else [10.0] * len(future),
        "future_dates": [f"2024-01-{3 + i:02d}" for i in range(len(future))],
        "base_bar": base_bar,
        "plan_date": trade.date,
    }
    bt._simulate(trade, future, ctx)
    return trade


# --------------------------------------------------------------------------- #
# 1. 限价成交可达性 —— 核心修复
# --------------------------------------------------------------------------- #
class TestLimitFillReachability:
    def test_never_touched_limit_is_not_filled(self):
        """全天最低价都高于委托价 → 买不到，不得凭空成交。"""
        t = _trade()
        fut = _future([(10.60, 10.80, 10.55, 10.70), (10.70, 10.90, 10.60, 10.80)])
        _sim(t, fut)
        assert t.entry_executed is False
        assert t.exit_reason == "not_entered"
        assert t.return_pct == 0.0

    def test_filled_when_low_touches_limit(self):
        t = _trade()
        fut = _future([(10.60, 10.80, 9.95, 10.30), (10.30, 11.00, 10.20, 10.90)])
        _sim(t, fut)
        assert t.entry_executed is True
        assert t.fill_type == "limit"
        assert t.entry_exec_price == pytest.approx(10.00)

    def test_filled_at_open_on_gap_down(self):
        """跳空低开在委托价之下 → 以开盘价成交（价格改善）。"""
        t = _trade()
        fut = _future([(9.60, 9.90, 9.50, 9.80)])
        _sim(t, fut)
        assert t.entry_executed is True
        assert t.fill_type == "open"
        assert t.entry_exec_price == pytest.approx(9.60)

    def test_fill_price_not_clamped_up_to_entry_low(self):
        """回归：成交价不得被强行抬到入场区间下沿。"""
        t = _trade(entry_low=9.80, entry_price=10.00)
        fut = _future([(9.00, 9.20, 8.90, 9.10)])
        _sim(t, fut)
        assert t.entry_exec_price == pytest.approx(9.00)   # 而不是 9.80


# --------------------------------------------------------------------------- #
# 2. 交易成本
# --------------------------------------------------------------------------- #
class TestCosts:
    def test_net_below_gross_and_cost_drag_positive(self):
        t = _trade()
        fut = _future([(10.00, 12.20, 9.90, 12.10)])
        _sim(t, fut)
        assert t.entry_executed
        assert t.gross_return_pct is not None
        assert t.return_pct < t.gross_return_pct
        assert t.cost_pct > 0
        assert t.gross_return_pct - t.return_pct == pytest.approx(t.cost_pct, abs=0.02)

    def test_cn_round_trip_cost_magnitude(self):
        """A 股双边成本应在 0.1%~0.25% 量级（佣金+印花税+过户费+滑点）。"""
        c = cost_model_for_market("CN")
        pct = c.round_trip_cost_pct(10.0, 10.0)
        assert 0.10 < pct < 0.25

    def test_us_cheaper_than_cn(self):
        assert (
            cost_model_for_market("US").round_trip_cost_pct(100, 100)
            < cost_model_for_market("CN").round_trip_cost_pct(10, 10)
        )

    def test_zero_cost_model_matches_gross(self):
        cfg = ExecutionConfig(cost_model=CostModel(
            commission_bps=0, min_commission=0, stamp_tax_bps=0,
            transfer_fee_bps=0, slippage_bps=0,
        ))
        t = _trade()
        fut = _future([(10.00, 11.00, 9.90, 10.90)])
        _sim(t, fut, bt=_bt(exec_config=cfg))
        assert t.return_pct == pytest.approx(t.gross_return_pct, abs=0.01)

    def test_min_commission_pct(self):
        c = cost_model_for_market("CN")
        assert c.min_commission_pct(1000.0) > 0     # 小额订单被最低佣金抬高
        assert c.min_commission_pct(10_000_000.0) == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 3. 跳空穿透
# --------------------------------------------------------------------------- #
class TestGapAwareExit:
    def test_stop_gap_fills_at_open(self):
        t = _trade()
        # 入场日开盘 10.00 成交；次日跳空低开到 9.05（低于止损 9.20，仍在 -10% 内）
        fut = _future([(10.00, 10.10, 9.90, 10.00), (9.05, 9.30, 9.00, 9.20)])
        _sim(t, fut, prev_closes=[10.0, 10.0])
        assert t.exit_reason == "stop_loss"
        assert t.gapped is True
        # 成交价应是开盘 9.05 而不是止损 9.20 → 亏损更大
        assert t.gross_return_pct == pytest.approx((9.05 / 10.00 - 1) * 100, abs=0.05)
        assert t.gross_return_pct < (9.20 / 10.00 - 1) * 100

    def test_gap_disabled_uses_stop_price(self):
        cfg = ExecutionConfig(gap_aware=False)
        t = _trade()
        fut = _future([(10.00, 10.10, 9.90, 10.00), (9.05, 9.30, 9.00, 9.20)])
        _sim(t, fut, bt=_bt(exec_config=cfg), prev_closes=[10.0, 10.0])
        assert t.gapped is False
        assert t.gross_return_pct == pytest.approx((9.20 / 10.00 - 1) * 100, abs=0.05)

    def test_target_gap_fills_at_open(self):
        t = _trade()
        # 次日跳空高开到 12.60（高于目标 12.00，仍在 +10% 内）
        fut = _future([(10.00, 10.10, 9.90, 10.00), (12.60, 12.80, 12.50, 12.70)])
        _sim(t, fut, prev_closes=[10.0, 10.0])
        assert t.exit_reason == "target_2"
        assert t.gapped is True
        assert t.gross_return_pct == pytest.approx((12.60 / 10.00 - 1) * 100, abs=0.05)


# --------------------------------------------------------------------------- #
# 4. 涨跌停 / 停牌
# --------------------------------------------------------------------------- #
class TestPriceLimitsAndSuspension:
    def test_price_limit_state_detects_one_word_limit_up(self):
        assert price_limit_state(10.0, 11.00, 10.99) == 1
        assert price_limit_state(10.0, 9.01, 9.00) == -1
        assert price_limit_state(10.0, 10.50, 9.80) == 0

    def test_limit_up_blocks_entry(self):
        """一字涨停买不进：全天最低价 = 涨停价。"""
        t = _trade(entry_price=10.00, entry_high=10.50)
        fut = _future([(11.00, 11.00, 10.99, 11.00), (11.00, 11.00, 10.99, 11.00)])
        _sim(t, fut, prev_closes=[10.00, 11.00])
        assert t.entry_executed is False
        assert t.limit_blocked_days >= 1

    def test_limit_down_defers_exit(self):
        """一字跌停卖不出：止损当日无法执行，顺延到可成交日。"""
        t = _trade()
        # 第 1 日按 10.00 入场；第 2 日一字跌停（9.00 封死，prev_close=10.00）；
        # 第 3 日可成交且跌破止损 9.20
        fut = _future([
            (10.00, 10.10, 9.90, 10.00),
            (9.00, 9.00, 8.99, 9.00),
            (9.10, 9.30, 9.05, 9.15),
        ])
        _sim(t, fut, prev_closes=[10.0, 10.0, 9.0])
        assert t.exit_reason == "stop_loss"
        # 跌停日不能成交 → 出场顺延到第 3 个 future bar（全局 bar = 0+1+2 = 3）
        assert t.exit_bar == 3
        assert t.gapped is True   # 开盘 9.10 < 止损 9.20

    def test_suspension_skips_day(self):
        t = _trade()
        fut = _future([(10.60, 10.80, 10.55, 10.70)], volume=0.0)
        _sim(t, fut)
        assert t.entry_executed is False
        assert t.limit_blocked_days >= 1

    def test_is_suspended_handles_missing_data(self):
        assert is_suspended(0, 0) is True
        assert is_suspended(1000, 1000) is False
        assert is_suspended(None, None) is False   # 缺失 ≠ 停牌

    def test_limit_pct_for_code(self):
        assert limit_pct_for_code("300750") == pytest.approx(0.20)
        assert limit_pct_for_code("688981") == pytest.approx(0.20)
        assert limit_pct_for_code("600519") == pytest.approx(0.10)


# --------------------------------------------------------------------------- #
# 5. 容量约束
# --------------------------------------------------------------------------- #
class TestCapacity:
    def test_large_order_scaled_down(self):
        t = _trade(planned_position_amount=10_000_000.0)   # 计划买 1000 万
        fut = _future([(10.00, 10.20, 9.95, 10.10)], volume=100_000)
        # 当日成交额 = 100000 * 10.10 ≈ 101 万；10% = 10.1 万 → 只能成交约 1%
        _sim(t, fut)
        assert t.capacity_limited is True
        assert t.capacity_fill_ratio < 0.05
        assert t.capital_weight == t.capacity_fill_ratio

    def test_small_order_not_limited(self):
        t = _trade(planned_position_amount=50_000.0)
        fut = _future([(10.00, 10.20, 9.95, 10.10)], volume=100_000)
        _sim(t, fut)
        assert t.capacity_limited is False
        assert t.capacity_fill_ratio == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 6. 重叠信号与组合净值
# --------------------------------------------------------------------------- #
class TestOverlapAndPortfolio:
    def test_portfolio_rejects_concurrent_signals(self):
        """并发上限 1 时，同期信号应被放弃而不是共用同一笔钱。"""
        trades = [
            {"entry_executed": True, "entry_date": "2024-01-02", "exit_date": "2024-01-10",
             "return_pct": 10.0, "capital_weight": 1.0},
            {"entry_executed": True, "entry_date": "2024-01-03", "exit_date": "2024-01-11",
             "return_pct": 10.0, "capital_weight": 1.0},
        ]
        pf = simulate_portfolio(trades, initial_capital=100_000, max_position_pct=0.5,
                               max_positions=1)
        assert pf["skipped"] == 1
        assert pf["n_trades"] == 1
        assert pf["total_return"] == pytest.approx(5.0, abs=0.01)   # 只赚到一笔 50%*10%

    def test_portfolio_no_double_counting(self):
        """两笔完全重叠的交易各占 50% 资金 → 组合收益应是加权和，不是复利连乘。"""
        trades = [
            {"entry_executed": True, "entry_date": "2024-01-02", "exit_date": "2024-01-20",
             "return_pct": 20.0, "capital_weight": 1.0},
            {"entry_executed": True, "entry_date": "2024-01-02", "exit_date": "2024-01-20",
             "return_pct": 20.0, "capital_weight": 1.0},
        ]
        pf = simulate_portfolio(trades, initial_capital=100_000, max_position_pct=0.5,
                               max_positions=5)
        # 50%*1.2 + 50%*1.2 = 1.2 → +20%，而不是 1.2*1.2 = +44%
        assert pf["total_return"] == pytest.approx(20.0, abs=0.05)
        assert pf["final_equity"] == pytest.approx(120_000, abs=50)

    def test_sequential_policy_skips_overlapping(self):
        df = _kline_for_run()
        bt = _bt(stride=1, overlap_policy="sequential")
        res = bt.run(df, "600000", "T")
        skipped = [t for t in res.trades if t.exit_reason == "overlap_skipped"]
        assert skipped, "sequential 模式应跳过重叠信号"
        assert all(t.overlaps_prior for t in skipped)

    def test_metrics_report_overlap_rate(self):
        df = _kline_for_run()
        res = _bt(stride=1).run(df, "600000", "T")
        assert res.metrics is not None
        assert 0.0 <= res.metrics.overlap_rate <= 1.0

    def test_overlap_policy_validation(self):
        with pytest.raises(ValueError):
            TradingPlanBacktest(overlap_policy="nope")


def _kline_for_run(n: int = 400, seed: int = 5) -> pd.DataFrame:
    from quant_trading_system.stock_analysis.indicators import add_all_indicators

    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0.02, 0.15, n))
    high = close * (1 + np.abs(rng.normal(0, 0.015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.015, n)))
    volume = rng.uniform(1e6, 5e6, n)
    df = pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": volume, "amount": volume * close,
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
    })
    return add_all_indicators(df)


# --------------------------------------------------------------------------- #
# 7. 指标口径
# --------------------------------------------------------------------------- #
class TestMetricsSemantics:
    def test_not_entered_excluded_from_win_rate(self):
        trades = [
            {"entry_executed": True, "exit_reason": "target_2", "return_pct": 8.0,
             "holding_days": 5, "hit_target_1": True, "hit_target_2": True},
            {"entry_executed": True, "exit_reason": "stop_loss", "return_pct": -3.0,
             "holding_days": 2, "hit_target_1": False, "hit_target_2": False},
            {"entry_executed": False, "exit_reason": "not_entered", "return_pct": 0.0,
             "holding_days": 0, "hit_target_1": False, "hit_target_2": False},
        ]
        m = calc_metrics(sample_size=3, entry_zone_hits=2, trades=trades)
        assert m.executed_trades == 2
        assert m.not_entered == 1
        assert m.win_rate == pytest.approx(0.5)          # 不是 1/3
        assert m.avg_return == pytest.approx(2.5)        # 不是 (8-3+0)/3

    def test_cost_drag_reported(self):
        trades = [
            {"entry_executed": True, "exit_reason": "target_2", "return_pct": 7.5,
             "gross_return_pct": 7.8, "holding_days": 5,
             "hit_target_1": True, "hit_target_2": True},
        ]
        m = calc_metrics(sample_size=1, entry_zone_hits=1, trades=trades)
        assert m.cost_drag == pytest.approx(0.3, abs=1e-6)
        assert m.gross_avg_return == pytest.approx(7.8)

    def test_metrics_to_dict_has_realism_keys(self):
        m = calc_metrics(sample_size=0, entry_zone_hits=0, trades=[])
        d = m.to_dict()
        for k in ("not_entered", "cost_drag", "capacity_limited_rate", "overlap_rate",
                  "gapped_stop_rate", "portfolio_total_return", "portfolio_max_drawdown"):
            assert k in d


# --------------------------------------------------------------------------- #
# 7. 最低佣金进入净收益（回归：小额订单成本被系统性低估）
# --------------------------------------------------------------------------- #
class TestMinCommissionInNetReturn:
    """回归缺陷：``min_commission_pct`` 存在但从未被 ``round_trip_cost_pct`` 使用，
    于是 1000 元的订单只按万 2.5 计佣金（0.025%），而实际最低收 5 元（0.5%）——
    小账户/小仓位的净收益被系统性高估。"""

    def test_small_notional_costs_more_than_large(self):
        c = CostModel(commission_bps=2.5, min_commission=5.0, stamp_tax_bps=5.0,
                      transfer_fee_bps=0.1, slippage_bps=0.0)
        small = c.round_trip_cost_pct(10.0, 10.0, notional=1_000.0)
        large = c.round_trip_cost_pct(10.0, 10.0, notional=10_000_000.0)
        assert small > large
        # 1000 元 → 佣金实际 0.5%（最低 5 元），比万 2.5 高 0.475pp，双边共 0.95pp
        assert small - large == pytest.approx(0.95, abs=0.01)

    def test_notional_omitted_keeps_old_behaviour(self):
        """不传 notional 时口径不变（向后兼容历史指标）。"""
        c = CostModel(slippage_bps=0.0)
        assert c.round_trip_cost_pct(10.0, 10.0) == pytest.approx(
            c.round_trip_cost_pct(10.0, 10.0, notional=10_000_000.0), abs=1e-9)

    def test_large_notional_no_min_commission_extra(self):
        c = CostModel(min_commission=5.0)
        assert c.min_commission_pct(10_000_000.0) == pytest.approx(0.0, abs=1e-12)

    def test_close_records_min_commission_contribution(self):
        """小额计划金额 → ``_close`` 必须把最低佣金补差记进 cost_pct 与字段。"""
        c = CostModel(commission_bps=2.5, min_commission=5.0, stamp_tax_bps=5.0,
                      transfer_fee_bps=0.1, slippage_bps=0.0)
        t = _trade(entry_executed=True, entry_exec_price=10.0,
                   planned_position_amount=1_000.0)
        TradingPlanBacktest._close(t, 10.0, "timeout", 1, 0, [], 0, c, gapped=False)
        assert t.min_commission_pct > 0
        assert t.cost_pct > c.round_trip_cost_pct(10.0, 10.0)   # 含最低佣金
        assert t.return_pct < 0                                  # 平价卖出也要亏手续费

    def test_close_falls_back_to_one_lot_when_amount_missing(self):
        """计划金额缺失时按「成交价 × 1 手」估算，不静默跳过最低佣金。"""
        c = CostModel(commission_bps=2.5, min_commission=5.0, slippage_bps=0.0)
        t = _trade(entry_executed=True, entry_exec_price=10.0)   # planned_position_amount=0
        TradingPlanBacktest._close(t, 10.0, "timeout", 1, 0, [], 0, c, gapped=False)
        assert t.min_commission_pct > 0


# --------------------------------------------------------------------------- #
# 8. 跨市场涨跌停口径（回归：港股/美股被套用 A 股 10% 封板）
# --------------------------------------------------------------------------- #
class TestMarketPriceLimits:
    """回归缺陷：``run()`` 对非 CN 市场也用 ``LIMIT_PCT_DEFAULT=0.10``，于是美股
    某天涨 10% 被判成「一字涨停 → 买不进」。港股、美股没有 A 股式日内涨跌停。"""

    def test_market_has_price_limits(self):
        from quant_trading_system.stock_analysis.backtest import market_has_price_limits

        assert market_has_price_limits("CN") is True
        assert market_has_price_limits("HK") is False
        assert market_has_price_limits("US") is False
        assert market_has_price_limits("XX") is True      # 未知市场保守按有

    def test_with_market_disables_limits_outside_cn(self):
        cfg = ExecutionConfig()
        assert cfg.with_market("CN").enforce_price_limits is True
        assert cfg.with_market("HK").enforce_price_limits is False
        assert cfg.with_market("US").enforce_price_limits is False
        # 显式关闭时仍然关闭
        assert ExecutionConfig(enforce_price_limits=False).with_market("CN") \
            .enforce_price_limits is False

    def test_bj_and_st_limit_pct(self):
        assert limit_pct_for_code("830799") == pytest.approx(0.30)   # 北交所
        assert limit_pct_for_code("430047") == pytest.approx(0.30)
        assert limit_pct_for_code("600519", is_st=True) == pytest.approx(0.05)
        assert limit_pct_for_code("300750", is_st=True) == pytest.approx(0.05)  # ST 优先

    def test_us_backtest_does_not_block_ten_percent_up_day(self):
        """美股 +10% 的交易日必须能买入（原先被判成一字涨停而拒单）。"""
        bt_us = TradingPlanBacktest(market="US", exec_config=ExecutionConfig())
        t = _trade(entry_price=110.0, entry_low=110.0, entry_high=110.0,
                   stop_loss=100.0, target_1=120.0, target_2=130.0)
        fut = _future([(110.0, 110.0, 110.0, 110.0), (110.0, 121.0, 110.0, 120.0)])
        _sim(t, fut, bt=bt_us, prev_closes=[100.0, 110.0])
        assert t.entry_executed is True
        assert t.limit_blocked_days == 0

    def test_cn_backtest_still_blocks_one_word_limit_up(self):
        """同一根 K 线在 A 股口径下必须仍然被拦（不能因为修 HK/US 而放开 CN）。"""
        bt_cn = TradingPlanBacktest(market="CN", exec_config=ExecutionConfig())
        t = _trade(entry_price=110.0, entry_low=110.0, entry_high=110.0,
                   stop_loss=100.0, target_1=120.0, target_2=130.0)
        fut = _future([(110.0, 110.0, 110.0, 110.0)])
        _sim(t, fut, bt=bt_cn, prev_closes=[100.0])
        assert t.entry_executed is False
        assert t.limit_blocked_days >= 1
