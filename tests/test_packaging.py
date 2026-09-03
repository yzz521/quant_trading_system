"""Ensure setuptools wheel includes nested stock_analysis subpackages."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_wheel_includes_stock_analysis_subpackages(tmp_path):
    root = _repo_root()
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(root), "-w", str(tmp_path), "--no-deps"],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("quant_trading_system-*.whl"))
    assert wheels, "wheel not built"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    required_prefixes = (
        "quant_trading_system/stock_analysis/opportunity/",
        "quant_trading_system/stock_analysis/market/",
        "quant_trading_system/stock_analysis/scoring/",
        "quant_trading_system/stock_analysis/ai/",
        "quant_trading_system/stock_analysis/backtest/",
        "quant_trading_system/dashboard/home.py",
        "quant_trading_system/dashboard/pages/0_opportunity.py",
        "quant_trading_system/dashboard/pages/1_holdings.py",
        "quant_trading_system/dashboard/pages/2_settings.py",
    )
    for prefix in required_prefixes:
        assert any(n.startswith(prefix) or n == prefix for n in names), f"missing in wheel: {prefix}"
