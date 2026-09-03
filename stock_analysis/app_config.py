"""User-facing notify.yaml helpers shared by the dashboard 配置页 and scheduler.

Does not import Streamlit. Password fields are never logged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from ..utils import deep_merge, load_yaml, save_yaml

ALL_MARKETS = ("CN", "HK", "US")
MARKET_LABELS = {"CN": "A股", "HK": "港股", "US": "美股"}
MARKET_LABELS_UI = {"CN": "🇨🇳 A股", "HK": "🇭🇰 港股", "US": "🇺🇸 美股"}
DEFAULT_ENABLED_MARKETS = ["CN"]

SMTP_PRESETS: dict[str, Optional[tuple[str, int, bool]]] = {
    "QQ 邮箱": ("smtp.qq.com", 465, True),
    "163 邮箱": ("smtp.163.com", 465, True),
    "Outlook": ("smtp.office365.com", 587, False),
    "Gmail": ("smtp.gmail.com", 465, True),
    "自定义": None,
}


def normalize_markets(raw: Any) -> list[str]:
    """Keep CN/HK/US in input order; empty/invalid → A股 only."""
    out: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return list(DEFAULT_ENABLED_MARKETS)
    for item in raw:
        code = str(item).strip().upper()
        if code in ALL_MARKETS and code not in out:
            out.append(code)
    return out or list(DEFAULT_ENABLED_MARKETS)


def enabled_markets(cfg: Optional[dict] = None) -> list[str]:
    cfg = cfg or {}
    return normalize_markets(cfg.get("enabled_markets"))


def parse_email_list(raw: str) -> list[str]:
    parts = [p.strip() for p in str(raw).replace(";", ",").replace("；", ",").replace("，", ",").split(",")]
    return [p for p in parts if p]


def parse_code_list(raw: str) -> list[str]:
    parts = [p.strip().upper() for p in str(raw).replace(";", ",").replace("，", ",").split(",")]
    return [p for p in parts if p]


def example_notify_path() -> Optional[Path]:
    here = Path(__file__).resolve()
    candidates = [here.parents[1] / "config" / "notify.yaml.example"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "config" / "notify.yaml.example")
    for p in candidates:
        if p.is_file():
            return p
    return None


def ensure_notify_file(path: str | Path) -> Path:
    dest = Path(path)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    example = example_notify_path()
    if example is not None:
        dest.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    save_yaml(
        dest,
        {
            "enabled_markets": list(DEFAULT_ENABLED_MARKETS),
            "stock_pools": {"CN": [], "US": [], "HK": []},
            "opportunity": {
                "enabled": False,
                "index_symbol": "sh000001",
                "max_stocks": 30,
                "account_equity": 100000,
                "workers": 5,
                "min_opportunity_score": 0,
            },
            "notify": {
                "email": {
                    "enabled": False,
                    "smtp_host": "smtp.qq.com",
                    "smtp_port": 465,
                    "use_ssl": True,
                    "username": "",
                    "password": "",
                    "sender_name": "GP助手",
                    "to": [],
                }
            },
            "schedule": {
                "cn_interval_min": 60,
                "ushk_interval_min": 10,
                "us_winter": True,
                "poll_interval_sec": 60,
            },
            "ai": {"enabled": False, "api_key": "", "base_url": "", "model": ""},
        },
    )
    return dest


def load_app_config(path: str | Path) -> dict:
    p = ensure_notify_file(path)
    data = load_yaml(p) or {}
    return data if isinstance(data, dict) else {}


def save_app_config(path: str | Path, updates: dict) -> dict:
    p = Path(path)
    cfg = load_app_config(p)
    deep_merge(cfg, updates)
    if "enabled_markets" in updates:
        cfg["enabled_markets"] = normalize_markets(updates["enabled_markets"])
    save_yaml(p, cfg)
    return cfg


def smtp_preset_name(host: str) -> str:
    host = (host or "").strip().lower()
    for name, spec in SMTP_PRESETS.items():
        if spec and spec[0] == host:
            return name
    return "自定义"


def apply_smtp_preset(name: str, email_cfg: dict) -> dict:
    spec = SMTP_PRESETS.get(name)
    if spec:
        email_cfg["smtp_host"] = spec[0]
        email_cfg["smtp_port"] = spec[1]
        email_cfg["use_ssl"] = spec[2]
    return email_cfg
