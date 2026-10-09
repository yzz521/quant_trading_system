"""调度器运行状态（results/scheduler_state.json）测试。

回归缺陷：``scheduler_state.record_run`` 写好了、``check_notify.py`` 也在读，
但**全项目没有任何调用方** —— 于是状态文件永远停在最后一次手工测试的时间，
排障时 ``check_notify.py`` 报出的「上次运行时间」是几周前的旧值，
足以让人得出「调度器早就死了」的错误结论。

本文件锁死三件事：状态写得进、读得出、而且**真的被调度器调用**。
"""
from __future__ import annotations

import re
from pathlib import Path

from quant_trading_system.stock_analysis.scheduler import MarketScheduler
from quant_trading_system.stock_analysis import scheduler_state as st

_REPO = Path(__file__).resolve().parents[1]


def _redirect_state(tmp_path, monkeypatch) -> Path:
    """把状态文件重定向到 tmp，避免测试污染真实 results/。"""
    target = tmp_path / "results" / "scheduler_state.json"
    monkeypatch.setattr(st, "_DEFAULT", target)
    return target


# --------------------------------------------------------------------------- #
# record_run / load_state / format_status_text
# --------------------------------------------------------------------------- #
class TestRecordRun:
    def test_writes_and_reads_back(self, tmp_path, monkeypatch):
        _redirect_state(tmp_path, monkeypatch)
        st.record_run("CN", ok=True, detail="holdings=4 actions=4",
                      holdings_n=4, actions_n=4, channels=["email"], notify_ok=True)

        state = st.load_state()
        cn = state["markets"]["CN"]
        assert cn["ok"] is True
        assert cn["holdings_n"] == 4
        assert cn["actions_n"] == 4
        assert cn["channels"] == ["email"]
        assert cn["notify_ok"] is True
        assert state["last_any_at"]

    def test_failure_sets_last_error_then_success_clears_it(self, tmp_path, monkeypatch):
        _redirect_state(tmp_path, monkeypatch)
        st.record_run("CN", ok=False, detail="执行失败: boom")
        assert st.load_state()["last_error"].startswith("[CN]")

        st.record_run("CN", ok=True, detail="holdings=4 actions=4")
        assert st.load_state()["last_error"] is None

    def test_missing_file_reads_as_empty(self, tmp_path, monkeypatch):
        _redirect_state(tmp_path, monkeypatch)
        state = st.load_state()
        assert state == {"markets": {}, "last_any_at": None, "last_error": None}

    def test_corrupt_file_does_not_raise(self, tmp_path, monkeypatch):
        target = _redirect_state(tmp_path, monkeypatch)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{ 不是合法 json", encoding="utf-8")
        assert st.load_state()["markets"] == {}

    def test_format_status_text_mentions_last_run(self, tmp_path, monkeypatch):
        _redirect_state(tmp_path, monkeypatch)
        st.record_run("CN", ok=True, detail="holdings=4 actions=4")
        text = st.format_status_text()
        assert "调度器最近状态" in text
        assert "[CN] OK" in text
        assert "holdings=4" in text


# --------------------------------------------------------------------------- #
# 调度器接线：_record 真的落盘，且失败不拖垮调度
# --------------------------------------------------------------------------- #
class TestSchedulerWiresRecordRun:
    def test_record_writes_state_file(self, tmp_path, monkeypatch):
        target = _redirect_state(tmp_path, monkeypatch)
        # _record 不依赖实例状态，直接以未绑定方式调用即可
        MarketScheduler._record(None, "CN", ok=True,
                                detail="holdings=4 actions=4",
                                holdings_n=4, actions_n=4)

        assert target.exists(), "调度器跑完一轮后状态文件必须落盘"
        assert st.load_state()["markets"]["CN"]["holdings_n"] == 4

    def test_record_records_failure(self, tmp_path, monkeypatch):
        _redirect_state(tmp_path, monkeypatch)
        MarketScheduler._record(None, "CN", ok=False, detail="执行失败: boom")
        assert st.load_state()["last_error"].startswith("[CN]")

    def test_record_never_raises(self, monkeypatch):
        """观测性只服务排障 —— 它自己炸了也不能中断调度。"""
        def boom(*a, **kw):
            raise RuntimeError("状态文件写不动了")

        monkeypatch.setattr(st, "record_run", boom)
        MarketScheduler._record(None, "CN", ok=True)  # 不应抛

    def test_run_forever_records_success_and_failure(self):
        src = (_REPO / "stock_analysis" / "scheduler.py").read_text(encoding="utf-8")
        assert "self._record(" in src
        # 成功与失败两条路径都要记
        assert re.search(r"ok=True", src)
        assert re.search(r"ok=False", src)

    def test_run_once_records(self):
        src = (_REPO / "stock_analysis" / "scheduler.py").read_text(encoding="utf-8")
        assert "self._run_and_record(" in src


class TestRecordRunIsNotDeadCode:
    def test_scheduler_calls_record_run(self):
        """回归：record_run 曾经全项目零调用，状态文件因此永远是旧值。"""
        src = (_REPO / "stock_analysis" / "scheduler.py").read_text(encoding="utf-8")
        assert "record_run" in src

    def test_check_notify_reads_state(self):
        """自检工具读的就是这个文件 —— 两边必须成对存在，否则排障结论是错的。"""
        src = (_REPO / "examples" / "check_notify.py").read_text(encoding="utf-8")
        assert "format_status_text" in src
