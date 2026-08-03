"""Resolve package-root config paths for both top-level apps and multipage scripts."""
from __future__ import annotations

from pathlib import Path

# dashboard/paths.py -> parents[1] = package root (quant_trading_system/)
_PKG_ROOT = Path(__file__).resolve().parents[1]


def package_root() -> Path:
    return _PKG_ROOT


def holdings_config() -> str:
    return str(_PKG_ROOT / "config" / "holdings.yaml")


def notify_config() -> str:
    return str(_PKG_ROOT / "config" / "notify.yaml")
