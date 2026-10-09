"""Utility helpers for the quantitative trading system."""
import os as _os
import sys as _sys

# python.org 版 Python（如 .venv 的 3.13）默认证书库可能为空，导致
# urllib/smtplib 等 HTTPS 校验失败；统一用 certifi 的 CA 包兜底。
try:
    import certifi as _certifi
    _os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
except Exception:  # noqa: BLE001
    pass

# 守护进程（调度器 / 实时盯盘）的 stdout、stderr 会被重定向进日志文件，
# 而 akshare 内部用 tqdm 打进度条，会把日志刷成
# 「Please wait for a moment: 12/70」这类噪声——实测把
# results/scheduler.err.log 刷到 1.4 MB，真正有用的告警被埋在里面。
# 非交互（stdout 不是 TTY）时统一关掉；终端里手动跑仍保留进度条。
#
# 注意：tqdm 是在 **import 时** 读 TQDM_DISABLE 的（不是构造 tqdm 对象时），
# 所以必须在任何第三方库 import tqdm 之前设好。本模块被 data_fetcher 等
# 在 akshare 之前导入，是可靠的早期挂载点；放到调用点再设是无效的。
if not _sys.stdout.isatty():
    _os.environ.setdefault("TQDM_DISABLE", "1")

from .calendar import get_trading_days, is_trading_day, next_trading_day
from .helpers import deep_merge, ensure_dir, load_json, load_yaml, pct_change, safe_round, save_yaml
from .logger import add_file_handler, get_logger, set_log_level

__all__ = [
    "get_logger",
    "set_log_level",
    "add_file_handler",
    "load_yaml",
    "save_yaml",
    "deep_merge",
    "load_json",
    "ensure_dir",
    "safe_round",
    "pct_change",
    "is_trading_day",
    "get_trading_days",
    "next_trading_day",
]
