"""Dashboard data paths — frozen exe uses QTS_DATA_DIR (exe_dir/config)."""
from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    """User config directory (holdings.db, notify.yaml, users.yaml)."""
    env = os.environ.get("QTS_DATA_DIR")
    if env:
        return Path(env)
    # dev: repo root / config
    return Path(__file__).resolve().parents[1] / "config"


def holdings_config() -> str:
    return str(config_dir() / "holdings.yaml")


def notify_config() -> str:
    return str(config_dir() / "notify.yaml")


def users_config() -> Path:
    return config_dir() / "users.yaml"
