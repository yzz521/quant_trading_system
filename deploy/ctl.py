#!/usr/bin/env python3
"""Cross-platform service controller for quant_trading_system.

Works on macOS / Linux / Windows without requiring bash.

Usage::

    python deploy/ctl.py start-all
    python deploy/ctl.py stop-all
    python deploy/ctl.py status
    python deploy/ctl.py dashboard start
    python deploy/ctl.py holdings stop
    python deploy/ctl.py scheduler start

Environment::

    PYTHON_BIN   Python interpreter (default: current)
    PORT         unified dashboard port (default 8502)
    HOLDINGS_PORT (default 8503)
    STOCK_PORT    (default 8504)
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

IS_WIN = sys.platform.startswith("win")


def _python() -> str:
    env = os.environ.get("PYTHON_BIN")
    if env:
        return env
    try:
        import streamlit  # noqa: F401
        return sys.executable
    except Exception:  # noqa: BLE001
        pass
    # 当前解释器缺 streamlit（看板必需）时，探测已安装 streamlit 的解释器
    candidates = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / ".venv" / "bin" / "python3"),
        "/opt/anaconda3/bin/python3",
        "/Users/yzz/.workbuddy/binaries/python/envs/default/bin/python",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
    ]
    for c in candidates:
        if not os.path.exists(c):
            continue
        try:
            r = subprocess.run(
                [c, "-c", "import streamlit"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode == 0:
                return c
        except Exception:  # noqa: BLE001
            continue
    return sys.executable


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
    "tunnel": {
        "desc": "Cloudflare 快速隧道（cloudflared → 本地看板）",
        "port_key": "dashboard",
        "cloudflared": True,
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

    if meta.get("cloudflared"):
        port = ports[meta["port_key"]]
        cf = os.environ.get("CLOUDFLARED_BIN", "cloudflared")
        cmd = [cf, "tunnel", "--url", f"http://127.0.0.1:{port}"]
        url_hint = f"cloudflared → http://127.0.0.1:{port}（公网 URL 见日志）"
    elif "module" in meta:
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
        if meta.get("cloudflared"):
            print("   提示: 在日志中搜索 trycloudflare.com 获取公网地址")
            print(f"   例如: grep -oE 'https://[a-zA-Z0-9.-]+.trycloudflare.com' {logf} | tail -1")
    else:
        print(f"❌ {name} 启动可能失败，请查看 {logf}")


def _stop_one(name: str) -> None:
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


def cmd_start_all(include_scheduler: bool = False, include_tunnel: bool = False) -> None:
    names = list(DEFAULT_ALL)
    if include_scheduler:
        names.append("scheduler")
    if include_tunnel:
        names.append("tunnel")
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


def cmd_restart_all(include_scheduler: bool = False, include_tunnel: bool = False) -> None:
    cmd_stop_all()
    time.sleep(1)
    cmd_start_all(include_scheduler=include_scheduler, include_tunnel=include_tunnel)


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
  python deploy/ctl.py tunnel start   # 后台 cloudflared
  python deploy/ctl.py start-all --with-tunnel
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
    parser.add_argument(
        "--with-tunnel",
        action="store_true",
        help="start-all 时同时后台启动 cloudflared 隧道",
    )
    args = parser.parse_args(argv)

    svc = args.service.lower().replace("_", "-")
    act = args.action.lower()

    if svc in ("start-all", "startall", "all"):
        cmd_start_all(include_scheduler=args.with_scheduler, include_tunnel=args.with_tunnel)
        return 0
    if svc in ("stop-all", "stopall"):
        cmd_stop_all()
        return 0
    if svc in ("restart-all", "restartall"):
        cmd_restart_all(include_scheduler=args.with_scheduler, include_tunnel=args.with_tunnel)
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
        _stop_one(svc)
        time.sleep(0.5)
        _start_one(svc)
    elif act == "log":
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
