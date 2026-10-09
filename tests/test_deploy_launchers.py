"""部署脚本接线：`./deploy/restart.sh` 必须默认把实时盯盘（快轨）一起拉起来。

背景
----
快轨只在进程被拉起时才工作。过去 `restart.sh` 只重启「调度器 + 看板」，用户日常
就是跑这个脚本 —— 于是 `realtime.enabled: true` 一直"配好了但从没在跑"，而界面上
看不出区别。这组用例把"默认加载"钉死，防止以后又被漏掉。

`ctl.py` 直接 import 后断言真实的数据结构（比匹配字符串可靠）；`restart.sh` 是
shell，只能做源码级断言 + `bash -n` 语法检查。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "deploy" / "ctl.py"
CTLSH = ROOT / "deploy" / "ctl.sh"
SPAWN = ROOT / "deploy" / "spawn.py"
RESTART = ROOT / "deploy" / "restart.sh"


def _load_ctl():
    spec = importlib.util.spec_from_file_location("_qts_deploy_ctl", CTL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def test_ctl_knows_the_realtime_service():
    mod = _load_ctl()
    assert "realtime" in mod.SERVICES
    meta = mod.SERVICES["realtime"]
    assert meta["script"] == "examples/run_realtime.py"
    assert meta["port_key"] is None       # 不是 Web 服务，只是常驻进程


def test_realtime_is_started_by_default():
    """start-all 默认就带快轨：未启用时常驻待命、零行情请求，所以"默认起"是安全的。"""
    mod = _load_ctl()
    assert "realtime" in mod.DEFAULT_ALL
    assert "dashboard" in mod.DEFAULT_ALL


def test_restart_sh_restarts_realtime_in_all_branch():
    src = RESTART.read_text(encoding="utf-8")
    assert "restart_realtime()" in src
    assert "ctl.py realtime restart" in src
    # all 分支必须真的调用它，否则"默认加载"只是写在注释里。
    # 判定「在 all 分支块内出现」而非匹配某一行的一字不差写法 ——
    # 后者会因为排版调整（单行改多行）而误报。
    m = re.search(r'^\s*all\|""\)(.*?)^\s*;;', src, re.S | re.M)
    assert m, "找不到 restart.sh 的 all 分支"
    block = m.group(1)
    for fn in ("restart_scheduler", "restart_dashboard", "restart_realtime"):
        assert fn in block, f"all 分支没有调用 {fn}"
    assert "realtime)" in src                     # 支持 ./deploy/restart.sh realtime


def test_restart_sh_falls_back_when_launchd_job_missing():
    """调度器只认 launchd 时，未 bootstrap 会静默失败 —— 必须回退到 nohup 方式。"""
    src = RESTART.read_text(encoding="utf-8")
    assert "ctl.py scheduler restart" in src


def test_restart_sh_scheduler_log_path_is_inside_project():
    """日志路径不能再用 $(dirname "$ROOT")/results/ —— 那会和 ctl.sh 各写一份。"""
    src = RESTART.read_text(encoding="utf-8")
    assert 'SCHED_LOG="$(dirname "$ROOT")' not in src
    assert 'SCHED_LOG="$ROOT/results/scheduler.log"' in src


def test_restart_sh_status_shows_heartbeat_not_just_pid():
    """进程活着 ≠ 在盯票：状态输出必须带引擎的心跳状态。"""
    src = RESTART.read_text(encoding="utf-8")
    assert "show_realtime_state()" in src
    assert "run_realtime.py\" --status" in src
    assert "RT_LOG" in src and "realtime.pid" in src


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash 语法检查仅 POSIX")
def test_restart_sh_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(RESTART)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(sys.platform.startswith("win"), reason="ctl 的 PID 探活仅 POSIX")
def test_ctl_env_flag_does_not_break_import():
    """ctl.py 只依赖标准库（跨平台控制器），不能因为快轨而引入 pandas/streamlit。"""
    mod = _load_ctl()
    assert mod.SERVICES["realtime"]["script"].endswith(".py")
    assert not hasattr(mod, "pandas")


# --------------------------------------------------------------------------- #
# ctl.sh：守护进程必须真正脱离父进程组
#
# 实测事故：`nohup cmd &` 起的调度器，启动日志正常打出、随后进程凭空消失
# （exit code 137）。原因是 nohup 只挡 SIGHUP，挡不住父 shell 退出时发给
# 整个进程组的信号。修复方式是改用 deploy/spawn.py（start_new_session=True）。
# 这两条守卫把修复钉死，防止以后又被改回 nohup。
# --------------------------------------------------------------------------- #
def _code_lines(path: Path) -> str:
    """只看代码行，跳过以 # 开头的注释 —— 否则注释里提到 nohup 就误报。"""
    return "\n".join(
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_spawn_py_detaches_from_process_group():
    src = SPAWN.read_text(encoding="utf-8")
    assert "start_new_session=True" in src
    assert "Popen" in src


def test_ctl_sh_starts_daemons_via_spawn_not_nohup():
    code = _code_lines(CTLSH)
    assert "spawn.py" in code, "ctl.sh 必须用 spawn.py 启动守护进程"
    assert "nohup" not in code, "ctl.sh 不能再用 nohup（挡不住进程组信号）"


def test_ctl_sh_restart_does_not_exec_itself_by_path():
    """restart 曾写成 `"$0" ...`，而仓库里 ctl.sh 是 644 → Permission denied。"""
    code = _code_lines(CTLSH)
    assert '"$0"' not in code
    assert 'bash "$SELF"' in code


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash 语法检查仅 POSIX")
def test_ctl_sh_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(CTLSH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
