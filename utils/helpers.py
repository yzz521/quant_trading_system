"""General-purpose helpers used across the trading system."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_yaml(path: str | Path) -> dict:
    """Load a YAML config file into a dict (lazy import to avoid hard dependency)."""
    import yaml  # type: ignore

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_round(value: float, ndigits: int = 4) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return round(float(value), ndigits)


def pct_change(old: float, new: float) -> float:
    if not old:
        return 0.0
    return (new - old) / old
