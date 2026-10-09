"""影子模式测试。

影子模式的价值全在于：**在零资金风险下把执行层的缺陷暴露出来**。
所以本文件把两件事钉死 ——
  1. 5 道风控闸门**各自**在越界时确实拦得住、在正常时确实放行（不能只会拦或只会放）；
  2. 执行层的订单集合与信号层的可执行信号**必须一一对应**（漏单/凭空下单/价格分叉都是 bug）。
"""
from __future__ import annotations

import pytest
from quant_trading_system.stock_analysis.opportunity import DecisionState, TradingPlan
from quant_trading_system.stock_analysis.shadow_orders import (
    COST_TO_RISK_WARN,
    OrderIntent,
    RiskGuardConfig,
    _cost_of,
    affordable_price_ceiling,
    build_shadow_orders,
    guard_connection,
    guard_daily_loss,
    guard_duplicate,
    guard_single_position,
    guard_total_position,
    load_shadow_orders,
    reconcile_with_signals,
    record_shadow_orders,
    recordable,
    summarize_shadow,
)

DATE = "2026-01-05"
TS = 1_700_000_000.0            # 固定的「当前时间」，让断线闸门可预测


def _plan(code="600519", decision="BUY_NOW", **kw):
    p = {"code": code, "name": "贵州茅台", "decision": decision,
         "entry_price": 100.0, "entry_low": 99.0, "entry_high": 101.0,
         "stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0,
         "risk_reward_1": 2.0, "position_percent": None}
    p.update(kw)
    return p


def _run(plans, **kw):
    """默认带一个「新鲜」的行情时间戳，避免所有用例都被断线闸门拦掉。"""
    kw.setdefault("quote_ts", TS)
    kw.setdefault("now", TS)
    kw.setdefault("date", DATE)
    return build_shadow_orders(plans, **kw)


# --------------------------------------------------------------------------- #
# 闸门 4 · 断线即停
# --------------------------------------------------------------------------- #
class TestGuardConnection:
    def test_missing_timestamp_is_disconnected(self):
        ok, why = guard_connection(None, now=TS)
        assert ok is False and "断线" in why

    def test_stale_quote_is_blocked(self):
        ok, why = guard_connection(TS - 300, now=TS)
        assert ok is False and "过期" in why

    def test_fresh_quote_passes(self):
        ok, why = guard_connection(TS - 10, now=TS)
        assert ok is True and why == ""

    def test_unparsable_timestamp_is_blocked(self):
        ok, _ = guard_connection("not-a-time", now=TS)
        assert ok is False

    def test_threshold_is_configurable(self):
        cfg = RiskGuardConfig(max_quote_staleness_sec=600)
        assert guard_connection(TS - 300, now=TS, cfg=cfg)[0] is True


# --------------------------------------------------------------------------- #
# 闸门 1 · 单日最大亏损
# --------------------------------------------------------------------------- #
class TestGuardDailyLoss:
    def test_loss_beyond_limit_blocks(self):
        ok, why = guard_daily_loss(97_000.0, 100_000.0)      # -3%
        assert ok is False and "停止当日新开仓" in why

    def test_loss_within_limit_passes(self):
        ok, _ = guard_daily_loss(98_000.0, 100_000.0)        # -2%
        assert ok is True

    def test_gain_passes(self):
        assert guard_daily_loss(101_000.0, 100_000.0)[0] is True

    def test_missing_baseline_does_not_block(self):
        """没有基准就不误伤 —— 保守不等于乱杀。"""
        assert guard_daily_loss(90_000.0, None)[0] is True
        assert guard_daily_loss(None, 100_000.0)[0] is True

    def test_zero_baseline_does_not_divide_by_zero(self):
        assert guard_daily_loss(90_000.0, 0.0)[0] is True


# --------------------------------------------------------------------------- #
# 闸门 2 · 单票上限
# --------------------------------------------------------------------------- #
class TestGuardSinglePosition:
    def test_held_plus_new_exceeds_cap(self):
        """必须算已持有部分 —— 只校验单笔会漏掉「反复加仓把仓位堆上去」。"""
        ok, why = guard_single_position(5_000.0, 18_000.0, 100_000.0)   # 23000 > 20000
        assert ok is False and "单票合计" in why

    def test_exactly_at_cap_passes(self):
        assert guard_single_position(20_000.0, 0.0, 100_000.0)[0] is True

    def test_no_equity_blocks(self):
        ok, why = guard_single_position(1_000.0, 0.0, None)
        assert ok is False and "无总资产" in why

    def test_cap_is_configurable(self):
        cfg = RiskGuardConfig(max_single_pct=0.30)
        assert guard_single_position(25_000.0, 0.0, 100_000.0, cfg=cfg)[0] is True


# --------------------------------------------------------------------------- #
# 闸门 3 · 总仓位上限
# --------------------------------------------------------------------------- #
class TestGuardTotalPosition:
    def test_total_exceeds_cap(self):
        ok, why = guard_total_position(90_000.0, 10_000.0, 100_000.0)   # 100000 > 95000
        assert ok is False and "总仓位" in why

    def test_within_cap_passes(self):
        assert guard_total_position(50_000.0, 10_000.0, 100_000.0)[0] is True

    def test_no_equity_blocks(self):
        assert guard_total_position(0.0, 1_000.0, None)[0] is False

    def test_cap_is_configurable(self):
        cfg = RiskGuardConfig(max_total_pct=0.50)
        assert guard_total_position(40_000.0, 5_000.0, 100_000.0, cfg=cfg)[0] is True


# --------------------------------------------------------------------------- #
# 闸门 5 · 幂等防重复下单
# --------------------------------------------------------------------------- #
class TestGuardDuplicate:
    def test_existing_key_is_duplicate(self):
        ok, why = guard_duplicate((DATE, "600519", "BUY"), [(DATE, "600519", "BUY")])
        assert ok is False and "重复下单" in why

    def test_different_side_is_not_duplicate(self):
        assert guard_duplicate((DATE, "600519", "BUY"), [(DATE, "600519", "SELL")])[0] is True

    def test_different_date_is_not_duplicate(self):
        assert guard_duplicate((DATE, "600519", "BUY"), [("2026-01-04", "600519", "BUY")])[0] is True

    def test_empty_ledger_passes(self):
        assert guard_duplicate((DATE, "600519", "BUY"), [])[0] is True


# --------------------------------------------------------------------------- #
# 成本（信息性，不是闸门）
# --------------------------------------------------------------------------- #
class TestCostInfo:
    def test_small_order_cost_ratio_is_high(self):
        """¥1000 的单子、1% 止损 → 成本 / 止损距离 > 1/3（决策条件 4 的反例）。"""
        cp, cr, warn = _cost_of(1_000.0, 10.0, 9.9, "CN")
        assert cp is not None and cr is not None
        assert cr > COST_TO_RISK_WARN and warn is True

    def test_large_order_cost_ratio_is_low(self):
        _cp, cr, warn = _cost_of(20_000.0, 100.0, 95.0, "CN")
        assert cr is not None and cr < COST_TO_RISK_WARN and warn is False

    def test_missing_amount_returns_none(self):
        assert _cost_of(None, 10.0, 9.0, "CN") == (None, None, False)


class TestAffordableCeiling:
    """「买得起的最高股价」把大量 SKIPPED 归结成一个可比较的阈值。"""

    def test_ceiling_from_equity(self):
        # 10,000 × 20% / 100 股 = 20 元/股
        assert affordable_price_ceiling(10_000.0) == 20.0

    def test_ceiling_respects_custom_cap_and_lot(self):
        assert affordable_price_ceiling(10_000.0, max_single_pct=0.30) == 30.0
        assert affordable_price_ceiling(10_000.0, lot_size=200) == 10.0

    def test_no_equity_returns_none(self):
        assert affordable_price_ceiling(None) is None
        assert affordable_price_ceiling(0) is None

    def test_summary_shows_ceiling(self):
        run = _run([_plan(entry_price=300.0, stop_loss=285.0)], equity=1_000.0)
        assert "买得起的最高股价" in run.summary()


# --------------------------------------------------------------------------- #
# 影子下单主流程
# --------------------------------------------------------------------------- #
class TestBuildShadowOrders:
    def test_ready_order_is_generated(self):
        run = _run([_plan()], equity=100_000.0)
        ready = run.ready()
        assert len(ready) == 1
        o = ready[0]
        assert (o.code, o.side, o.status) == ("600519", "BUY", "READY")
        assert o.quantity == 200 and o.amount == 20_000.0
        assert o.shadow is True and o.to_dict()["shadow"] is True

    def test_tradingplan_enum_decision_is_recognised(self):
        """DecisionState 是 (str, Enum)：走 str() 会得到 "DecisionState.BUY_NOW"，
        所有计划都会被静默漏掉。这里钉死必须走 .value。"""
        plan = TradingPlan(code="600519", name="贵州茅台", decision=DecisionState.BUY_NOW,
                           entry_price=100.0, stop_loss=95.0, target_1=110.0,
                           risk_reward_1=2.0)
        run = _run([plan], equity=100_000.0)
        assert len(run.orders) == 1 and run.orders[0].status == "READY"

    def test_non_actionable_decision_is_ignored(self):
        run = _run([_plan(decision="WATCH"), _plan(code="000001", decision="AVOID")],
                   equity=100_000.0)
        assert run.orders == []

    def test_no_equity_skips_everything(self):
        run = _run([_plan()], equity=None)
        assert len(run.orders) == 1
        assert run.orders[0].status == "SKIPPED" and "无总资产" in run.orders[0].blocked_by

    def test_zero_quantity_is_skipped(self):
        """账户太小：一手就超过单票上限 → 仓位建议为 0，必须跳过而不是硬凑一手。"""
        run = _run([_plan(entry_price=300.0, stop_loss=285.0)], equity=1_000.0)
        assert run.orders[0].status == "SKIPPED"
        assert "仓位建议为 0" in run.orders[0].blocked_by

    def test_connection_blocks_all(self):
        run = _run([_plan()], equity=100_000.0, quote_ts=None, now=TS)
        assert run.ready() == []
        assert "断线" in run.orders[0].blocked_by

    def test_daily_loss_blocks_all(self):
        run = _run([_plan()], equity=96_000.0, equity_prev_close=100_000.0)
        assert run.ready() == []
        assert "daily_loss" in run.orders[0].blocked_by

    def test_single_position_blocks(self):
        run = _run([_plan()], equity=100_000.0, held_by_code={"600519": 15_000.0})
        assert run.ready() == []
        assert "single_position" in run.orders[0].blocked_by

    def test_total_position_blocks(self):
        run = _run([_plan()], equity=100_000.0, holdings_value=80_000.0)
        assert run.ready() == []
        assert "total_position" in run.orders[0].blocked_by

    def test_duplicate_in_ledger_blocks(self):
        run = _run([_plan()], equity=100_000.0,
                   existing_keys=[(DATE, "600519", "BUY")])
        assert run.ready() == []
        assert "duplicate" in run.orders[0].blocked_by

    def test_duplicate_within_same_run_blocks_second(self):
        """同一轮里同一只票出现两次 → 第二笔必须被拦（否则一次跑出两笔委托）。"""
        run = _run([_plan(), _plan()], equity=100_000.0)
        assert len(run.ready()) == 1
        assert len(run.blocked()) == 1
        assert "duplicate" in run.blocked()[0].blocked_by

    def test_running_total_accumulates_across_orders(self):
        """同轮多笔必须逐笔累加，否则「每笔都不超总仓位」但合计超了。"""
        plans = [_plan(code="600519"), _plan(code="000001"), _plan(code="600036"),
                 _plan(code="601398"), _plan(code="600104"), _plan(code="002415")]
        run = _run(plans, equity=100_000.0)      # 每笔 20000 → 第 5 笔起合计 > 95000
        assert len(run.ready()) == 4
        assert len(run.blocked()) == 2
        assert all("total_position" in o.blocked_by for o in run.blocked())

    def test_cost_warning_is_flagged_on_small_order(self):
        run = _run([_plan(entry_price=10.0, stop_loss=9.9)], equity=5_000.0,
                   max_position_pct=0.20)
        o = run.orders[0]
        assert o.status == "READY" and o.cost_warning is True
        assert o.cost_to_risk is not None and o.cost_to_risk > COST_TO_RISK_WARN
        assert any("成本警告" in n for n in o.notes)

    def test_summary_is_renderable(self):
        run = _run([_plan()], equity=100_000.0)
        text = run.summary()
        assert "影子模式" in text and "600519" in text

    def test_run_to_dict_counts(self):
        run = _run([_plan(), _plan(code="000001", decision="WATCH")], equity=100_000.0)
        d = run.to_dict()
        assert d["n_ready"] == 1 and d["n_skipped"] == 0 and d["n_blocked"] == 0


# --------------------------------------------------------------------------- #
# 台账
# --------------------------------------------------------------------------- #
class TestLedger:
    def test_record_and_reload(self, tmp_path):
        run = _run([_plan()], equity=100_000.0)
        fresh = record_shadow_orders(run.orders, root=tmp_path)
        assert len(fresh) == 1
        rows = load_shadow_orders(tmp_path)
        assert len(rows) == 1 and rows[0]["code"] == "600519"

    def test_record_is_idempotent(self, tmp_path):
        run = _run([_plan()], equity=100_000.0)
        record_shadow_orders(run.orders, root=tmp_path)
        assert record_shadow_orders(run.orders, root=tmp_path) == []
        assert len(load_shadow_orders(tmp_path)) == 1

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_shadow_orders(tmp_path) == []

    def test_corrupt_line_is_skipped(self, tmp_path):
        p = tmp_path / "shadow_orders.jsonl"
        p.write_text('{"date":"2026-01-05","code":"600519","side":"BUY"}\n'
                     '{broken json\n', encoding="utf-8")
        rows = load_shadow_orders(tmp_path)
        assert len(rows) == 1 and rows[0]["code"] == "600519"

    def test_summarize(self, tmp_path):
        run = _run([_plan(), _plan(code="000001", decision="WATCH")], equity=100_000.0)
        record_shadow_orders(run.orders, root=tmp_path)
        s = summarize_shadow(tmp_path)
        assert s["n_total"] == 1 and s["by_status"].get("READY") == 1
        assert s["dates"] == [DATE]

    def test_summarize_empty(self, tmp_path):
        s = summarize_shadow(tmp_path)
        assert s["n_total"] == 0 and s["last_generated_at"] is None


class TestRecordable:
    """台账只收「非瞬时」的拦截：断线/当日亏损是暂时的，写进去会永久占住幂等键。"""

    def test_transient_blocks_are_excluded(self):
        run = _run([_plan()], equity=100_000.0, quote_ts=None, now=TS)
        assert run.orders[0].status == "BLOCKED"
        assert recordable(run) == []

    def test_daily_loss_block_is_excluded(self):
        run = _run([_plan()], equity=96_000.0, equity_prev_close=100_000.0)
        assert recordable(run) == []

    def test_persistent_blocks_are_kept(self):
        run = _run([_plan()], equity=100_000.0, held_by_code={"600519": 15_000.0})
        assert len(recordable(run)) == 1
        assert "single_position" in recordable(run)[0].blocked_by

    def test_ready_orders_are_kept(self):
        run = _run([_plan()], equity=100_000.0)
        assert len(recordable(run)) == 1

    def test_transient_block_does_not_lock_the_key(self, tmp_path):
        """断线那轮不入账 → 恢复后同一笔仍能正常写入（否则对账永远报漏单）。"""
        broken = _run([_plan()], equity=100_000.0, quote_ts=None, now=TS)
        assert record_shadow_orders(recordable(broken), root=tmp_path) == []

        healthy = _run([_plan()], equity=100_000.0,
                       existing_keys=[(r["date"], r["code"], r["side"])
                                      for r in load_shadow_orders(tmp_path)])
        assert record_shadow_orders(recordable(healthy), root=tmp_path) != []
        assert load_shadow_orders(tmp_path)[0]["status"] == "READY"


# --------------------------------------------------------------------------- #
# 对账：执行层 vs 信号层
# --------------------------------------------------------------------------- #
def _sig(code, entry=100.0):
    return {"signal_date": DATE, "code": code, "entry_price": entry}


def _ord(code, price=100.0, status="READY"):
    return OrderIntent(date=DATE, code=code, price=price, status=status, side="BUY")


class TestReconcile:
    def test_matching_sets_are_ok(self):
        r = reconcile_with_signals([_ord("600519")], [_sig("600519")])
        assert r["ok"] is True and r["n_signals"] == 1 and r["n_orders"] == 1

    def test_missing_order_is_reported(self):
        """有信号却没生成订单 = 执行层漏单，必须报出来。"""
        r = reconcile_with_signals([_ord("600519")], [_sig("600519"), _sig("000001")])
        assert r["ok"] is False and r["missing_orders"] == ["000001"]

    def test_extra_order_is_reported(self):
        """没信号却生成了订单 = 执行层凭空下单，比漏单更危险。"""
        r = reconcile_with_signals([_ord("600519"), _ord("000001")], [_sig("600519")])
        assert r["ok"] is False and r["extra_orders"] == ["000001"]

    def test_price_mismatch_is_reported(self):
        r = reconcile_with_signals([_ord("600519", price=101.0)], [_sig("600519", entry=100.0)])
        assert r["ok"] is False and r["price_mismatch"][0]["code"] == "600519"

    def test_price_within_tolerance_is_ok(self):
        r = reconcile_with_signals([_ord("600519", price=100.005)], [_sig("600519", entry=100.0)])
        assert r["ok"] is True

    def test_blocked_orders_are_excluded(self):
        """被闸门拦下的订单不算「该下的单」，不该造成 extra 误报。"""
        r = reconcile_with_signals([_ord("000001", status="BLOCKED")], [_sig("600519")])
        assert r["extra_orders"] == [] and r["missing_orders"] == ["600519"]

    def test_skipped_signal_is_infeasible_not_missing(self):
        """有信号、执行层也考虑了但因账户规模拒绝 → 是「不可行」，**不是漏单**。

        混为一谈会让真 bug 被噪声淹没：账户太小导致的拒绝每天都会出现，
        而漏单是必须立刻修的代码缺陷。
        """
        orders = [_ord("600519", status="SKIPPED")]
        r = reconcile_with_signals(orders, [_sig("600519")])
        assert r["missing_orders"] == []
        assert r["infeasible"] == {"600519": "SKIPPED"}
        assert r["n_infeasible"] == 1
        assert r["ok"] is True

    def test_infeasible_does_not_break_ok(self):
        orders = [_ord("600519", status="SKIPPED"), _ord("000001", status="BLOCKED")]
        sigs = [_sig("600519"), _sig("000001")]
        r = reconcile_with_signals(orders, sigs)
        assert r["ok"] is True and r["n_infeasible"] == 2

    def test_signal_without_any_intent_is_missing(self):
        """连一条意图都没产生（执行层完全没看到这个信号）→ 真·漏单。"""
        r = reconcile_with_signals([_ord("600519")], [_sig("600519"), _sig("300450")])
        assert r["missing_orders"] == ["300450"] and r["infeasible"] == {}

    def test_date_filter(self):
        other = {"signal_date": "2026-01-06", "code": "000001", "entry_price": 10.0}
        r = reconcile_with_signals([_ord("600519")], [_sig("600519"), other], date=DATE)
        assert r["ok"] is True and r["n_signals"] == 1


# --------------------------------------------------------------------------- #
# 端到端：计划 → 台账 → 对账
# --------------------------------------------------------------------------- #
class TestEndToEnd:
    def test_full_shadow_cycle(self, tmp_path):
        plans = [_plan(code="600519"), _plan(code="000001", entry_price=50.0, stop_loss=47.5)]
        run = _run(plans, equity=100_000.0)
        record_shadow_orders(run.orders, root=tmp_path)

        signals = [_sig("600519", 100.0), _sig("000001", 50.0)]
        r = reconcile_with_signals(load_shadow_orders(tmp_path), signals, date=DATE)
        assert r["ok"] is True, r
        assert summarize_shadow(tmp_path)["by_status"]["READY"] == 2

    def test_second_run_same_day_is_blocked_by_duplicate(self, tmp_path):
        """调度器一天跑多轮：第二轮必须被幂等闸门拦下，否则仓位翻倍。"""
        run1 = _run([_plan()], equity=100_000.0)
        record_shadow_orders(run1.orders, root=tmp_path)

        rows = load_shadow_orders(tmp_path)
        keys = [(r["date"], r["code"], r["side"]) for r in rows]
        run2 = _run([_plan()], equity=100_000.0, existing_keys=keys)
        assert run2.ready() == [] and "duplicate" in run2.orders[0].blocked_by

    def test_shadow_never_produces_sell_without_decision(self):
        """本模块只处理可执行买入决策；不能凭空生成卖出。"""
        run = _run([_plan(decision="SELL")], equity=100_000.0)
        assert run.orders == []


@pytest.mark.parametrize("bad", [None, 0, -1])
def test_no_equity_variants_are_skipped(bad):
    run = _run([_plan()], equity=bad)
    assert run.orders[0].status == "SKIPPED"


# --------------------------------------------------------------------------- #
# 调度器接入（旁路观测：失败绝不能影响推送）
# --------------------------------------------------------------------------- #
class TestSchedulerHook:
    @staticmethod
    def _sched(tmp_path):
        from quant_trading_system.stock_analysis.scheduler import MarketScheduler
        from quant_trading_system.utils import save_yaml

        path = tmp_path / "notify.yaml"
        save_yaml(path, {
            "enabled_markets": ["CN"],
            "stock_pools": {"CN": ["600519"]},
            "notify": {"email": {"enabled": False}},
            "schedule": {"poll_interval_sec": 60},
        })
        return MarketScheduler(str(path))

    def test_shadow_tick_records_ready_order(self, tmp_path, monkeypatch):
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        sched = self._sched(tmp_path)
        sched._shadow_tick([_plan()], "CN", {"total_capital": 100_000.0}, [])

        rows = SO.load_shadow_orders(tmp_path)
        assert len(rows) == 1
        assert rows[0]["code"] == "600519" and rows[0]["status"] == "READY"
        assert rows[0]["shadow"] is True

    def test_shadow_tick_is_idempotent_within_a_day(self, tmp_path, monkeypatch):
        """调度器一天跑多轮：第二轮不能再写一条（否则台账会重复计数）。"""
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        sched = self._sched(tmp_path)
        for _ in range(3):
            sched._shadow_tick([_plan()], "CN", {"total_capital": 100_000.0}, [])
        assert len(SO.load_shadow_orders(tmp_path)) == 1

    def test_shadow_tick_skips_without_equity(self, tmp_path, monkeypatch):
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        sched = self._sched(tmp_path)
        sched._shadow_tick([_plan()], "CN", None, [])
        assert SO.load_shadow_orders(tmp_path) == []

    def test_shadow_tick_swallows_bad_input(self, tmp_path, monkeypatch):
        """旁路观测：垃圾输入必须被吞掉，绝不能把调度主流程带崩。"""
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        sched = self._sched(tmp_path)
        sched._shadow_tick([_plan()], "CN", {"total_capital": "not-a-number"}, None)
        sched._shadow_tick("不是列表", "CN", {"total_capital": 100_000.0}, None)
        assert SO.load_shadow_orders(tmp_path) == []

    def test_shadow_tick_counts_held_positions(self, tmp_path, monkeypatch):
        """已持有该票时，单票闸门要能拦住 —— 否则会反复加仓把仓位堆上去。

        快照必须带上 ``available_cash``：影子模式的 equity 走「市值 + 现金」口径
        （与组合风控同一把尺子），只给 ``total_capital`` 会让净值退化成持仓市值
        本身，仓位先被算成 0，就测不到单票闸门了。
        """
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        sched = self._sched(tmp_path)
        holdings = [{"code": "600519", "quantity": 150, "cost_price": 100.0}]
        # 净值 = 15,000(市值) + 70,000(现金) = 85,000；单票上限 20% = 17,000，
        # 而该票已占 15,000 —— 再买 17,000 必然超限。
        snap = {"total_capital": 100_000.0, "available_cash": 70_000.0}
        sched._shadow_tick([_plan()], "CN", snap, holdings)

        rows = SO.load_shadow_orders(tmp_path)
        assert len(rows) == 1 and rows[0]["status"] == "BLOCKED"
        assert "single_position" in rows[0]["blocked_by"]
