"""GP助手 · 桌面应用壳（pywebview + 内嵌 Streamlit + 定时推送并入）。

架构：
  * pywebview 原生窗口（macOS WKWebView / Windows WebView2 / Linux WebKitGTK）
  * 后台线程启动 Streamlit 看板（http://127.0.0.1:PORT）
  * 后台线程运行 MarketScheduler（每日邮件定时推送，开窗即调度、关窗即停）
  * 窗口关闭 → 优雅停止两个后台服务

用法：
  * 开发模式:  python app/main.py
  * 打包:      pyinstaller app/packaging/gp_assistant.spec （三端）

平台依赖：
  * macOS: 无需额外（WKWebView 内置）
  * Windows: 需 Edge WebView2 运行时（Win10/11 通常已带）
  * Linux:   sudo apt install python3-gi gir1.2-webkit2-4.1
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# 路径与日志（打包后 _MEIPASS 与用户数据目录分离）
# --------------------------------------------------------------------------- #
APP_NAME = "GP助手"


def _base_dir() -> Path:
    """资源目录：开发=仓库根；PyInstaller 打包=解包目录 _MEIPASS（放 dashboard 脚本）。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # noqa: SLF001
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    """数据目录（config/holdings/results）：开发=仓库根；打包=可执行文件旁。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config"
    return Path(__file__).resolve().parents[1] / "config"


BASE = _base_dir()
DATA_DIR = Path(os.environ.get("QTS_DATA_DIR", str(_data_dir())))
RESULTS_DIR = DATA_DIR.parent / "results" if DATA_DIR.name == "config" else BASE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(RESULTS_DIR / "app.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("App")

PORT = int(os.environ.get("PORT", "8502"))
# 单实例锁端口（与 streamlit 端口独立；绑定失败 = 已有实例在运行）
LOCK_PORT = int(os.environ.get("LOCK_PORT", "8503"))
# 打包后不能用 `exe -m streamlit`：那会再启动一份 GUI（v0.3.5 单例锁会立刻杀掉子进程，
# 看板永远起不来）。用哨兵参数让同一个 exe 走 Streamlit 子进程分支。
DASHBOARD_FLAG = "--run-dashboard"

# 锁 socket 需保活（否则 GC 后端口释放，锁失效）
_LOCK_SOCKET: socket.socket | None = None


def acquire_singleton() -> bool:
    """独占绑定锁端口；返回 False 说明已有 GP助手 实例在运行。

    注意：不要设置 SO_REUSEADDR——macOS/Windows 上它允许第二个监听
    socket 绑定同一端口（劫持），会破坏单例锁的排他性。锁 socket 从不
    accept 连接，进程退出即释放端口，无需处理 TIME_WAIT。
    """
    global _LOCK_SOCKET  # noqa: PLW0603
    # 幂等：本进程已持有锁则直接成功（避免先覆盖引用导致旧 socket 被 GC 关闭、锁被释放）
    if _LOCK_SOCKET is not None:
        return True
    try:
        _LOCK_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _LOCK_SOCKET.bind(("127.0.0.1", LOCK_PORT))
        _LOCK_SOCKET.listen(1)
        return True
    except OSError:
        _LOCK_SOCKET = None
        return False


def _alert(title: str, msg: str) -> None:
    """Windows 弹窗提示；其他平台打印到 stderr。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x10)  # MB_ICONERROR
    else:
        print(f"[{title}] {msg}", file=sys.stderr)


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _wait_port(port: int, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _kill_process_tree(proc: subprocess.Popen | None) -> None:
    """终止进程及其整棵子进程树（Windows 用 taskkill /T，避免子进程残留堆积）。"""
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _dashboard_script() -> Path:
    """Streamlit 入口脚本（必须是磁盘上的 .py；打包后在 _MEIPASS 下）。"""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS"))  # noqa: SLF001
        roots = [
            meipass / "quant_trading_system" / "dashboard",
            meipass / "dashboard",
        ]
    else:
        roots = [Path(__file__).resolve().parents[1] / "dashboard"]
    for root in roots:
        p = root / "首页.py"
        if p.is_file():
            return p
    tried = ", ".join(str(r / "首页.py") for r in roots)
    raise FileNotFoundError(f"找不到看板入口 首页.py，已试: {tried}")


def _parse_dashboard_argv(argv: list[str]) -> tuple[int, Path]:
    """解析 `[exe, --run-dashboard, PORT, SCRIPT]`。"""
    idx = argv.index(DASHBOARD_FLAG)
    port = int(argv[idx + 1])
    script = Path(argv[idx + 2])
    return port, script


def _run_dashboard_child(argv: list[str]) -> None:
    """冻结 exe 作为看板子进程：只跑 Streamlit，不创建窗口、不抢单例锁。"""
    try:
        port, script = _parse_dashboard_argv(argv)
    except (ValueError, IndexError):
        print(f"invalid {DASHBOARD_FLAG} argv: {argv}", file=sys.stderr)
        sys.exit(2)
    if not script.is_file():
        print(f"dashboard script missing: {script}", file=sys.stderr)
        sys.exit(3)
    from streamlit.web import bootstrap

    bootstrap.run(
        str(script),
        False,
        [],
        {
            "server.port": port,
            "server.address": "127.0.0.1",
            "server.headless": True,
            "browser.gatherUsageStats": False,
            "server.fileWatcherType": "none",
            "global.developmentMode": False,
        },
    )


# --------------------------------------------------------------------------- #
# 后台服务线程
# --------------------------------------------------------------------------- #
class DashboardServer:
    """子进程运行 Streamlit 看板（官方推荐方式，兼容 PyInstaller 打包）。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        try:
            script = _dashboard_script()
        except FileNotFoundError as e:
            log.error("看板脚本缺失: %s", e)
            self.proc = None
            return
        if getattr(sys, "frozen", False):
            # 同一个冻结 exe，走 --run-dashboard 分支（不要 -m streamlit）
            cmd = [sys.executable, DASHBOARD_FLAG, str(self.port), str(script)]
        else:
            # 开发模式：仓库根 = quant_trading_system 包，父目录进 PYTHONPATH
            env["PYTHONPATH"] = str(BASE.parent) + os.pathsep + env.get("PYTHONPATH", "")
            cmd = [
                sys.executable, "-m", "streamlit", "run",
                str(script),
                "--server.port", str(self.port),
                "--server.address", "127.0.0.1",
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
            ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(script.parent),
                env=env,
                stdout=open(RESULTS_DIR / "dashboard.log", "a", encoding="utf-8"),
                stderr=subprocess.STDOUT,
            )
            log.info("看板子进程已启动 pid=%s script=%s", self.proc.pid, script)
        except Exception as e:  # noqa: BLE001
            log.error("看板子进程启动失败: %s", e)
            self.proc = None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            _kill_process_tree(self.proc)
            log.info("看板子进程已停止")


class SchedulerThread(threading.Thread):
    """后台线程运行每日邮件调度器（开窗即调度，关窗即停）。"""

    def __init__(self, config_path: Path) -> None:
        super().__init__(daemon=True, name="scheduler")
        self.config_path = config_path
        self._stop = threading.Event()
        self._sched_ready = False

    def run(self) -> None:
        sys.path.insert(0, str(BASE.parent))
        try:
            from quant_trading_system.stock_analysis.scheduler import MarketScheduler

            # 首次运行：config/notify.yaml 不存在时从 example 初始化
            # （example 在资源目录 _MEIPASS/config/，打包后与数据目录分离）
            if not self.config_path.exists():
                example = BASE / "config" / "notify.yaml.example"
                if example.exists():
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    self.config_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
                    log.info("已从 notify.yaml.example 初始化推送配置")
                else:
                    log.warning("缺少推送配置 %s，调度器跳过（可配置后重启）", self.config_path)
                    return

            sched = MarketScheduler(str(self.config_path))
            self._sched_ready = True
            while not self._stop.is_set():
                try:
                    sched.run_once()
                except Exception as e:  # noqa: BLE001
                    log.error("调度执行失败: %s", e)
                self._stop.wait(sched.poll_interval)
            log.info("调度器已停止")
        except Exception as e:  # noqa: BLE001
            log.error("调度器启动失败: %s", e)

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def main() -> None:
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        log.exception("加载窗口组件失败")
        _alert(
            "缺少运行库",
            "无法加载桌面窗口组件。\n\n"
            f"{type(e).__name__}: {e}\n\n"
            "Windows 请安装 Edge WebView2 Runtime：\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/",
        )
        sys.exit(1)

    # 单实例锁：已有实例在运行则提示退出，避免多实例反复拉起子进程把内存吃满
    if not acquire_singleton():
        log.warning("已有 GP助手 实例在运行，本次启动退出")
        _alert("GP助手已在运行", "检测到已有 GP助手 正在运行。\n请先在任务管理器确认，或关闭旧实例后再启动。")
        sys.exit(0)

    # 端口占用则自动换空闲端口（避免与旧实例冲突）
    port = PORT
    if not _port_free(port):
        log.warning("端口 %d 被占用，自动选择空闲端口", port)
        for p in range(PORT + 1, PORT + 50):
            if _port_free(p):
                port = p
                break

    dashboard = DashboardServer(port)
    dashboard.start()

    config_path = DATA_DIR / "notify.yaml"
    scheduler = SchedulerThread(config_path)
    scheduler.start()

    if not _wait_port(port):
        log.error("看板服务未就绪，退出")
        dashboard.stop()
        _alert(
            "看板启动失败",
            "GP助手 看板服务未能启动。\n\n"
            "常见原因与处理：\n"
            "1. 内存不足：关闭其他大程序后重试\n"
            "2. 杀毒软件拦截：临时退出 360/火绒/Defender 实时防护\n"
            "3. 若反复出现，请查看日志：" + str(RESULTS_DIR / "app.log"),
        )
        sys.exit(1)
    log.info("看板就绪: http://127.0.0.1:%d", port)

    window = webview.create_window(
        f"{APP_NAME} · 每日决策",
        f"http://127.0.0.1:{port}",
        width=1360,
        height=900,
        min_size=(1100, 720),
        confirm_close=False,
    )

    def on_closed() -> None:
        log.info("窗口关闭，停止后台服务")
        scheduler.stop()
        dashboard.stop()

    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    if DASHBOARD_FLAG in sys.argv:
        _run_dashboard_child(sys.argv)
    else:
        try:
            main()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("启动失败")
            _alert(
                "GP助手启动失败",
                f"{type(e).__name__}: {e}\n\n日志: {RESULTS_DIR / 'app.log'}",
            )
            sys.exit(1)
