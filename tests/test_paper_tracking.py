"""纸面跟踪测试。

纸面跟踪是「不花钱检验系统信号」的唯一路径，它的价值全在于**记录不可事后修改**、
**结算口径与回测一致**。本文件把这两点钉死。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from quant_trading_system.stock_analysis.paper_tracking import (
    load_settlements,
    load_signals,
    open_signals,
    record_signals,
    settle_signals,
    settlements_path,
    signals_path,
    summarize,
)

SIG_DATE = "2026-01-05"


def _plan(code="600519", decision="BUY_NOW", **kw):
    p = {"code": code, "name": "贵州茅台", "decision": decision,
         "entry_price": 100.0, "entry_low": 99.0, "entry_high": 101.0,
         "stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0,
         "risk_reward_1": 2.0, "confidence": 70.0,
         "stock_score": 65.0, "opportunity_score": 75.0}
    p.update(kw)
    return p


def _bars(dates, closes, *, opens=None, highs=None, lows=None):
    closes = np.asarray(closes, dtype=float)
    opens = np.asarray(opens if opens is not None else closes, dtype=float)
    highs = np.asarray(highs if highs is not None else closes * 1.001, dtype=float)
    lows = np.asarray(lows if lows is not None else closes * 0.999, dtype=float)
    return pd.DataFrame({
        "date": pd.to_datetime(list(dates)),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.full(len(closes), 1e6),
        "amount": closes * 1e6,
    })


# --------------------------------------------------------------------------- #
# 记录信号
# --------------------------------------------------------------------------- #
class TestRecordSignals:
    def test_only_tracked_decisions(self, tmp_path):
        record_signals(
            [_plan("600519"), _plan("000001", decision="WATCH"),
             _plan("000002", decision="AVOID")],
            date=SIG_DATE, root=tmp_path,
        )
        codes = {s["code"] for s in load_signals(tmp_path)}
        assert codes == {"600519"}

    def test_is_idempotent_per_date_and_code(self, tmp_path):
        """同一天调度器会跑多轮，重复记录会把一笔交易算成好几笔。"""
        for _ in range(3):
            record_signals([_plan("600519")], date=SIG_DATE, root=tmp_path)
        assert len(load_signals(tmp_path)) == 1

    def test_same_code_on_different_days_is_two_signals(self, tmp_path):
        record_signals([_plan("600519")], date="2026-01-05", root=tmp_path)
        record_signals([_plan("600519")], date="2026-01-06", root=tmp_path)
        assert len(load_signals(tmp_path)) == 2

    def test_skips_blank_code(self, tmp_path):
        record_signals([_plan("")], date=SIG_DATE, root=tmp_path)
        assert load_signals(tmp_path) == []

    def test_accepts_object_plans(self, tmp_path):
        class P:
            code = "600519"
            name = "贵州茅台"
            decision = "BUY_NOW"
            entry_price = 100.0
            entry_low = 99.0
            entry_high = 101.0
            stop_loss = 95.0
            target_1 = 110.0
            target_2 = 120.0
            risk_reward_1 = 2.0
            confidence = 70.0
            stock_score = 65.0
            opportunity_score = 75.0

        record_signals([P()], date=SIG_DATE, root=tmp_path)
        assert load_signals(tmp_path)[0]["code"] == "600519"

    def test_records_signal_snapshot(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        rec = load_signals(tmp_path)[0]
        for k in ("signal_date", "code", "entry_price", "stop_loss",
                  "target_1", "risk_reward_1", "recorded_at"):
            assert k in rec, k
        assert rec["signal_date"] == SIG_DATE

    def test_appends_never_rewrites(self, tmp_path):
        """append-only：老记录的字节不能被后来的写入改动。"""
        record_signals([_plan("600519")], date="2026-01-05", root=tmp_path)
        first = signals_path(tmp_path).read_text(encoding="utf-8")
        record_signals([_plan("000001")], date="2026-01-06", root=tmp_path)
        after = signals_path(tmp_path).read_text(encoding="utf-8")
        assert after.startswith(first)

    def test_tolerates_corrupt_line(self, tmp_path):
        p = signals_path(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"code": "600519", "signal_date": "2026-01-05"}\n不是 json\n',
                     encoding="utf-8")
        assert len(load_signals(tmp_path)) == 1


# --------------------------------------------------------------------------- #
# 未了结信号
# --------------------------------------------------------------------------- #
class TestOpenSignals:
    def test_excludes_settled(self, tmp_path):
        record_signals([_plan("600519")], date="2026-01-05", root=tmp_path)
        record_signals([_plan("000001")], date="2026-01-06", root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)

        done = {(s["signal_date"], s["code"]) for s in load_settlements(tmp_path)}
        assert done, "前置条件：应至少结算出一笔"
        remaining = open_signals(root=tmp_path)
        assert all((s["signal_date"], s["code"]) not in done for s in remaining)
        assert len(remaining) == len(load_signals(tmp_path)) - len(done)

    def test_sorted_by_date(self, tmp_path):
        record_signals([_plan("000001")], date="2026-01-07", root=tmp_path)
        record_signals([_plan("600519")], date="2026-01-05", root=tmp_path)
        dates = [s["signal_date"] for s in open_signals(root=tmp_path)]
        assert dates == sorted(dates)


# --------------------------------------------------------------------------- #
# 结算
# --------------------------------------------------------------------------- #
def _loader_flat():
    """价格横盘且始终在入场区间内 → 必然成交，最终因持有期耗尽离场。"""
    def load(code, days=500):
        n = 80
        dates = pd.bdate_range(SIG_DATE, periods=n)
        return _bars(dates, np.full(n, 100.0))
    return load


def _loader_target_hit():
    """先在入场区间内成交，随后一路上涨触发 target_1。

    注意第一根未来 bar 的开盘价必须落在 [entry_low, entry_high] 内，
    否则会走 ``not_entered`` 分支（那正是另一个用例要测的）。
    """
    def load(code, days=500):
        closes = [100.0, 100.0, 100.0] + [100.0 + 3.0 * i for i in range(1, 20)]
        dates = pd.bdate_range(SIG_DATE, periods=len(closes))
        return _bars(dates, closes)
    return load


def _loader_never_enters():
    """价格远高于入场区间上沿 → 永远不会成交。"""
    def load(code, days=500):
        n = 80
        dates = pd.bdate_range(SIG_DATE, periods=n)
        return _bars(dates, np.full(n, 500.0))
    return load


def _rising_closes():
    """先三根停在入场区间内（确保成交），随后一路上涨触发 target_1。"""
    return [100.0, 100.0, 100.0] + [100.0 + 3.0 * i for i in range(1, 20)]


def _loader_real_shape():
    """模拟**真实**行情结构：index 是 DatetimeIndex、且没有 ``date`` 列。

    ``fetch_kline`` → ``add_all_indicators`` 返回的就是这个形状。早先
    ``_settle_one`` 是按「带 date 列的 Series」写的，而 ``pd.to_datetime``
    作用在 DatetimeIndex 上返回的仍是 DatetimeIndex（没有 ``.iloc``）——
    真实数据一到就 AttributeError，只因所有假 df 都带 date 列，测试全绿。
    这条 loader 专门钉死真实形状。
    """
    def load(code, days=500):
        closes = _rising_closes()
        df = _bars(pd.bdate_range(SIG_DATE, periods=len(closes)), closes)
        return df.set_index("date")          # 去掉 date 列，改用 DatetimeIndex
    return load


class TestSettleSignals:
    def test_settles_and_records_outcome(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)
        sets = load_settlements(tmp_path)
        assert len(sets) == 1
        s = sets[0]
        assert s["code"] == "600519"
        assert s["entry_executed"] is True
        assert s["exit_reason"]
        assert isinstance(s["return_pct"], float)

    def test_target_hit_yields_positive_net_return(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)
        s = load_settlements(tmp_path)[0]
        assert s["return_pct"] > 0

    def test_not_entered_is_recorded_too(self, tmp_path):
        """「信号看起来能买、实际买不到」必须留证 —— 这正是回测最容易高估的地方。"""
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_never_enters(), root=tmp_path)
        sets = load_settlements(tmp_path)
        assert len(sets) == 1
        assert sets[0]["entry_executed"] is False

    def test_settlement_is_idempotent(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)
        assert len(load_settlements(tmp_path)) == 1

    def test_loader_failure_does_not_raise(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)

        def bad(code, days=500):
            raise RuntimeError("接口抽风")

        assert settle_signals(loader=bad, root=tmp_path) == []
        assert load_settlements(tmp_path) == []

    def test_loader_returning_none_does_not_raise(self, tmp_path):
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        assert settle_signals(loader=lambda code, days=500: None, root=tmp_path) == []

    def test_no_signals_is_a_noop(self, tmp_path):
        assert settle_signals(loader=_loader_flat(), root=tmp_path) == []

    def test_accepts_real_market_data_shape(self, tmp_path):
        """真实行情是 DatetimeIndex + 无 date 列，不能只支持带 date 列的假 df。"""
        record_signals([_plan()], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_real_shape(), root=tmp_path)
        sets = load_settlements(tmp_path)
        assert len(sets) == 1
        assert sets[0]["entry_executed"] is True
        assert sets[0]["return_pct"] > 0

    def test_one_bad_symbol_does_not_drop_the_whole_batch(self, tmp_path):
        """单只票数据异常不能带走整批：写盘在循环内逐个进行，一抛就全丢是旧行为。"""
        record_signals(
            [_plan("600519"), _plan("000001"), _plan("000002")],
            date=SIG_DATE, root=tmp_path,
        )

        def load(code, days=500):
            df = _loader_real_shape()(code, days)
            if code == "000001":
                return df.drop(columns=["close"])   # 缺 close → 结算时抛 KeyError
            return df

        settled = settle_signals(loader=load, root=tmp_path)
        assert {s["code"] for s in settled} == {"600519", "000002"}
        assert len(load_settlements(tmp_path)) == 2


# --------------------------------------------------------------------------- #
# 成绩单
# --------------------------------------------------------------------------- #
class TestSummarize:
    def test_empty(self, tmp_path):
        s = summarize(root=tmp_path)
        assert s["n_signals"] == 0 and s["n_trades"] == 0
        assert s["win_rate"] is None

    def test_counts_and_rates(self, tmp_path):
        record_signals([_plan("600519"), _plan("000001")], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_target_hit(), root=tmp_path)
        s = summarize(root=tmp_path)
        assert s["n_signals"] == 2
        assert s["n_settled"] == 2
        assert s["n_trades"] == 2
        assert s["n_not_entered"] == 0
        assert s["win_rate"] == 1.0
        assert s["avg_return_pct"] > 0
        assert s["max_drawdown_pct"] <= 0
        assert s["first_signal_date"] == SIG_DATE

    def test_not_entered_not_counted_as_trade(self, tmp_path):
        record_signals([_plan("600519")], date=SIG_DATE, root=tmp_path)
        settle_signals(loader=_loader_never_enters(), root=tmp_path)
        s = summarize(root=tmp_path)
        assert s["n_settled"] == 1
        assert s["n_trades"] == 0
        assert s["n_not_entered"] == 1
        assert s["win_rate"] is None          # 没有成交样本，胜率无从谈起


# --------------------------------------------------------------------------- #
# 接线
# --------------------------------------------------------------------------- #
class TestSchedulerWiring:
    def test_scheduler_records_and_settles(self):
        src = (Path(__file__).resolve().parents[1] / "stock_analysis"
               / "scheduler.py").read_text(encoding="utf-8")
        assert "record_signals(" in src
        assert "settle_signals(" in src
        assert "self._track_paper(" in src

    def test_settle_is_throttled_to_once_per_day(self):
        src = (Path(__file__).resolve().parents[1] / "stock_analysis"
               / "scheduler.py").read_text(encoding="utf-8")
        assert "_last_settle_date" in src

    def test_paths_are_under_results(self, tmp_path):
        assert signals_path().name == "paper_signals.jsonl"
        assert settlements_path().name == "paper_settlements.jsonl"
        assert signals_path().parent.name == "results"
