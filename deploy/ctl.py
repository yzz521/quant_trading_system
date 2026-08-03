#!/usr/bin/env python3
"""Cross-platform service controller for quant_trading_system.

Works on macOS / Linux / Windows without requiring bash.

* macOS：调度器自动接入 launchd 托管（开机自启 + 崩溃自动拉起），
  `scheduler restart` 即重启并加载最新代码
* Linux / Windows：用内置后台进程管理

Usage::

    python deploy/ctl.py start-all
    python deploy/ctl.py start-all --with-scheduler
    python deploy/ctl.py stop-all
    python deploy/ctl.py status
    python deploy/ctl.py dashboard start
    python deploy/ctl.py holdings stop
    python deploy/ctl.py scheduler start|stop|status|restart|log
    ./deploy/ctl.py scheduler restart      # 也可直接执行（自动识别系统）

Environment::

    PYTHON_BIN   Python interpreter (default: current)
    PORT         unified dashboard port (default 8502)
    HOLDINGS_PORT (default 8503)
    STOCK_PORT    (default 8504)
"""
from __future__ import annotations

import argparse
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

IS_WIN = sys.platform.startswith("win")

# ------------------------------------------------------------------ #
# macOS launchd 集成：调度器统一由 launchd 托管（开机自启 + 崩溃自动拉起）
# ------------------------------------------------------------------ #
_LAUNCHD_LABEL = "com.gp.stock-scheduler"
_HINT = "python deploy/ctl.py"


def _launchd_plist() -> Path | None:
    """调度器 launchd 配置文件（~/.local/Library/LaunchAgents 已安装时）。"""
    if not sys.platform.startswith("darwin"):
        return None
    p = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
    return p if p.exists() else None


def _launchd_info() -> tuple[str, int | None]:
    """返回 (state, pid)：state∈{'loaded','not_loaded'}；pid 为 None 表示已注册但未运行。"""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return "not_loaded", None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == _LAUNCHD_LABEL:
            pid = parts[0]
            return "loaded", (int(pid) if pid.isdigit() else None)
    return "not_loaded", None


def _launchd_log_path() -> Path | None:
    pl = _launchd_plist()
    if pl is None:
        return None
    try:
        data = plistlib.loads(pl.read_bytes())
        path = data.get("StandardOutPath", "")
        return Path(path) if path else None
    except Exception:  # noqa: BLE001
        return None


def _kill_pid(pid: int) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except OSError:
        pass


# ---- 调度器 launchd 动作 --------------------------------------- #
def _scheduler_launchd_start() -> None:
    pl = _launchd_plist()
    # 防双实例：清理手动/残留实例，避免重复推送
    mp = _is_running("scheduler")
    if mp:
        _kill_pid(mp)
        _pid_file("scheduler").unlink(missing_ok=True)
        print(f"已清理手动实例 PID={mp}（避免与 launchd 重复推送）")
    state, _ = _launchd_info()
    if state == "not_loaded":
        subprocess.run(["launchctl", "load", str(pl)], check=False)
        time.sleep(1)
        _, pid = _launchd_info()
        if pid:
            print(f"✅ scheduler 已注册并启动（launchd 托管）PID={pid}  开机自启+崩溃自动拉起")
        else:
            print("❌ launchd 加载失败，请检查 ~/Library/LaunchAgents/ 下 plist")
        return
    pid = _launchd_info()[1]
    if pid:
        print(f"✅ scheduler 已在运行（launchd 托管）PID={pid}")
    else:
        subprocess.run(["launchctl", "kickstart",
                        f"gui/{os.getuid()}/{_LAUNCHD_LABEL}"], check=False)
        time.sleep(2)
        print(f"✅ scheduler 已启动（launchd 托管）PID={_launchd_info()[1] or '?'}")


def _scheduler_launchd_stop() -> None:
    pl = _launchd_plist()
    subprocess.run(["launchctl", "unload", str(pl)], check=False)
    print("✅ scheduler 已停止（launchd 已卸载；恢复请运行 scheduler start）")


def _scheduler_launchd_restart() -> None:
    if _launchd_info()[0] == "not_loaded":
        _scheduler_launchd_start()
        return
    subprocess.run(["launchctl", "kickstart", "-k",
                    f"gui/{os.getuid()}/{_LAUNCHD_LABEL}"], check=False)
    time.sleep(2)
    print(f"✅ scheduler 已重启（launchd 托管）PID={_launchd_info()[1] or '?'}  → 已加载最新代码")


def _scheduler_launchd_status() -> None:
    state, pid = _launchd_info()
    if state == "not_loaded":
        print(f"❌ scheduler  未加载（launchd）→ 运行 `{_HINT} scheduler start` 注册并启动")
    elif pid:
        print(f"✅ scheduler  运行中（launchd 托管）PID={pid}  开机自启+崩溃自动拉起")
    else:
        print(f"⚠️ scheduler  已注册但未运行 → 运行 `{_HINT} scheduler restart` 启动")
    mp = _is_running("scheduler")
    if mp:
        print(f"⚠️ 另发现手动实例 PID={mp}（非 launchd 托管）→ 运行 `{_HINT} scheduler start` 自动清理，避免重复推送")


def _scheduler_launchd_log() -> int:
    logp = _launchd_log_path() or _log_file("scheduler")
    if not logp.exists():
        print(f"无日志: {logp}")
        return 1
    print(logp.read_text(encoding="utf-8", errors="replace")[-8000:])
    return 0


def _python() -> str:
    return os.environ.get("PYTHON_BIN") or sys.executable


def _ports() -> dict[str, int]:
    return {
        "dashboard": int(os.environ.get("PORT", "8502")),
    }


SERVICES = {
    "dashboard": {
        "desc": "全部功能（持仓/卖出/诊断/回测）",
        "module": "dashboard/首页.py",
        "port_key": "dashboard",
    },
    "scheduler": {
        "desc": "分析推送调度器",
        "script": "examples/run_scheduler.py",
        "port_key": None,
    },
}

DEFAULT_ALL = ["dashboard"]  # 仅一个 Web 端口；调度器需 --with-scheduler  # scheduler 需 notify 配置，默认不自动起


def _pid_file(name: str) -> Path:
    return RESULTS / f"{name}.pid"


def _log_file(name: str) -> Path:
    return RESULTS / f"{name}.log"


def _is_running(name: str) -> int | None:
    pf = _pid_file(name)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        return None
    try:
        if IS_WIN:
            # tasklist filter
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            if str(pid) in out.stdout and "INFO:" not in out.stdout:
                return pid
            return None
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def _start_one(name: str) -> None:
    if name not in SERVICES:
        print(f"未知服务: {name}")
        return
    if name == "scheduler" and _launchd_plist():
        _scheduler_launchd_start()
        return
    running = _is_running(name)
    if running:
        print(f"✅ {name} 已在运行 PID={running}")
        return

    meta = SERVICES[name]
    py = _python()
    ports = _ports()
    env = os.environ.copy()
    # ensure package importable
    parent = str(ROOT.parent)
    env["PYTHONPATH"] = parent + os.pathsep + env.get("PYTHONPATH", "")

    if "module" in meta:
        port = ports[meta["port_key"]]
        cmd = [
            py, "-m", "streamlit", "run", str(ROOT / meta["module"]),
            "--server.port", str(port),
            "--server.headless", "true",
        ]
        url_hint = f"http://localhost:{port}"
    else:
        cmd = [py, str(ROOT / meta["script"])]
        url_hint = None

    logf = _log_file(name)
    stdout = open(logf, "a", encoding="utf-8")
    kwargs = {
        "cwd": str(ROOT),
        "env": env,
        "stdout": stdout,
        "stderr": subprocess.STDOUT,
    }
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    _pid_file(name).write_text(str(proc.pid), encoding="utf-8")
    time.sleep(1.5)
    if _is_running(name):
        print(f"✅ {name} 已启动 PID={proc.pid}  — {meta['desc']}")
        if url_hint:
            print(f"   浏览器: {url_hint}")
        print(f"   日志: {logf}")
    else:
        print(f"❌ {name} 启动可能失败，请查看 {logf}")


def _stop_one(name: str) -> None:
    if name == "scheduler" and _launchd_plist():
        _scheduler_launchd_stop()
        return
    pid = _is_running(name)
    pf = _pid_file(name)
    if not pid:
        print(f"{name} 未在运行")
        if pf.exists():
            pf.unlink(missing_ok=True)
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except OSError as e:
        print(f"停止 {name} 时出错: {e}")
    if pf.exists():
        pf.unlink(missing_ok=True)
    print(f"✅ {name} 已停止")


def _status_one(name: str) -> None:
    if name == "scheduler" and _launchd_plist():
        _scheduler_launchd_status()
        return
    pid = _is_running(name)
    meta = SERVICES.get(name, {})
    desc = meta.get("desc", "")
    if pid:
        port_key = meta.get("port_key")
        extra = ""
        if port_key:
            extra = f"  port={_ports()[port_key]}"
        print(f"✅ {name:12} 运行中 PID={pid}{extra}  {desc}")
    else:
        print(f"❌ {name:12} 未运行  {desc}")


def cmd_start_all(include_scheduler: bool = False) -> None:
    names = list(DEFAULT_ALL)
    if include_scheduler:
        names.append("scheduler")
    print("启动服务:", ", ".join(names))
    for n in names:
        _start_one(n)
    print("\n完成。打开浏览器: http://localhost:%s" % _ports()["dashboard"])
    print("（左侧切换：持仓与卖出 / 个股诊断 / 研究工具）")


def cmd_stop_all() -> None:
    for n in list(SERVICES.keys()):
        _stop_one(n)


def cmd_status() -> None:
    for n in SERVICES:
        _status_one(n)
    # 调度器最近一次运行（若有 state 文件）
    try:
        from quant_trading_system.stock_analysis.scheduler_state import format_status_text
        print()
        print(format_status_text())
    except Exception:
        pass



def cmd_restart_all(include_scheduler: bool = False) -> None:
    cmd_stop_all()
    time.sleep(1)
    cmd_start_all(include_scheduler=include_scheduler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="quant_trading_system 跨平台服务控制",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python deploy/ctl.py start-all
  python deploy/ctl.py start-all --with-scheduler
  python deploy/ctl.py stop-all
  python deploy/ctl.py status
  python deploy/ctl.py dashboard start
  python deploy/ctl.py holdings log
  python deploy/ctl.py scheduler restart    # macOS: 重启加载最新代码（launchd 托管）
  ./deploy/ctl.py scheduler status         # 直接执行，自动识别系统类型
""",
    )
    parser.add_argument(
        "service",
        nargs="?",
        default="status",
        help="服务名或 start-all/stop-all/status/restart-all",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="start",
        help="start|stop|status|restart|log",
    )
    parser.add_argument(
        "--with-scheduler",
        action="store_true",
        help="start-all 时同时启动调度器",
    )
    args = parser.parse_args(argv)

    svc = args.service.lower().replace("_", "-")
    act = args.action.lower()

    if svc in ("start-all", "startall", "all"):
        cmd_start_all(include_scheduler=args.with_scheduler)
        return 0
    if svc in ("stop-all", "stopall"):
        cmd_stop_all()
        return 0
    if svc in ("restart-all", "restartall"):
        cmd_restart_all(include_scheduler=args.with_scheduler)
        return 0
    if svc in ("status", "status-all"):
        cmd_status()
        return 0
    if svc in ("help", "-h", "--help"):
        parser.print_help()
        return 0

    if svc not in SERVICES:
        # compat: bare start/stop -> scheduler
        if svc in ("start", "stop", "restart", "log") and act == "start":
            act = svc
            svc = "scheduler"
        else:
            print(f"未知服务: {svc}")
            parser.print_help()
            return 1

    if act == "start":
        _start_one(svc)
    elif act == "stop":
        _stop_one(svc)
    elif act == "status":
        _status_one(svc)
    elif act == "restart":
        if svc == "scheduler" and _launchd_plist():
            _scheduler_launchd_restart()
        else:
            _stop_one(svc)
            time.sleep(0.5)
            _start_one(svc)
    elif act == "log":
        if svc == "scheduler" and _launchd_plist():
            return _scheduler_launchd_log()
        logf = _log_file(svc)
        if not logf.exists():
            print(f"无日志: {logf}")
            return 1
        print(logf.read_text(encoding="utf-8", errors="replace")[-8000:])
    else:
        print(f"未知动作: {act}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
