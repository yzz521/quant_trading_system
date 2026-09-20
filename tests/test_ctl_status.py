"""ctl.py 服务状态探测：launchd 托管（无 pid 文件）场景下 pgrep 兜底。"""
from __future__ import annotations

import deploy.ctl as ctl


def _pgrep_result(stdout: str):
    class _R:
        pass

    r = _R()
    r.stdout = stdout
    return r


def test_proc_running_via_pgrep(monkeypatch):
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: _pgrep_result("80146\n"))
    monkeypatch.setattr(ctl.os, "kill", lambda pid, sig: None)
    assert ctl._proc_running("scheduler") == 80146


def test_proc_running_empty(monkeypatch):
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: _pgrep_result(""))
    assert ctl._proc_running("scheduler") is None


def test_proc_running_skips_dead_pid(monkeypatch):
    # pgrep 给出一个已死 pid（os.kill 抛 OSError）→ 应返回 None
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: _pgrep_result("99999\n"))

    def _kill(pid, sig):
        raise OSError("no such process")

    monkeypatch.setattr(ctl.os, "kill", _kill)
    assert ctl._proc_running("scheduler") is None


def test_is_running_falls_back_to_proc(monkeypatch, tmp_path):
    # 无 pid 文件，靠 pgrep 兜底正确识别运行中（修复 launchd 误报「未运行」）
    monkeypatch.setattr(ctl, "_pid_file", lambda name: tmp_path / f"{name}.pid")
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: _pgrep_result("80146\n"))
    monkeypatch.setattr(ctl.os, "kill", lambda pid, sig: None)
    assert ctl._is_running("scheduler") == 80146


def test_is_running_no_proc_no_pidfile(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl, "_pid_file", lambda name: tmp_path / f"{name}.pid")
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: _pgrep_result(""))
    assert ctl._is_running("scheduler") is None
