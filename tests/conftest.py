"""Ensure the package is importable as quant_trading_system.* during tests.

Layout: this folder is the package root (contains core/, strategy/, ...).
Parent directory must be on sys.path so ``import quant_trading_system`` works.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
_PARENT = _PKG_ROOT.parent

# Parent so ``quant_trading_system`` resolves to this directory when the
# folder is named quant_trading_system; also add package root for flat runs.
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
