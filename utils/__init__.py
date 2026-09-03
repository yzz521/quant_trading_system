"""Utility helpers for the quantitative trading system."""
import os as _os

# python.org 版 Python（如 .venv 的 3.13）默认证书库可能为空，导致
# urllib/smtplib 等 HTTPS 校验失败；统一用 certifi 的 CA 包兜底。
try:
    import certifi as _certifi
    _os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
except Exception:  # noqa: BLE001
    pass

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
