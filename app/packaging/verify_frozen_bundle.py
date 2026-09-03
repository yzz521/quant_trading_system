#!/usr/bin/env python3
"""Post-PyInstaller checks — fail the build before the user runs a broken exe."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"VERIFY FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"VERIFY OK: {msg}")


def _warn(msg: str) -> None:
    print(f"VERIFY WARN: {msg}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/GPAssistant")
    internal = root / "_internal"
    if not internal.is_dir():
        _fail(f"missing _internal under {root}")

    if not list(internal.rglob("streamlit-*.dist-info")):
        _fail("streamlit dist-info missing")

    if not list(internal.rglob("cacert.pem")):
        _fail("certifi cacert.pem missing")
    if not list(internal.rglob("libssl*.dll")):
        _fail("libssl*.dll missing")
    if not list(internal.rglob("libcrypto*.dll")):
        _fail("libcrypto*.dll missing")

    dash_dir = internal / "quant_trading_system" / "dashboard"
    dash = dash_dir / "home.py"
    if not dash.is_file():
        _fail(f"dashboard entry missing: {dash}")
    page_opp = dash_dir / "pages" / "0_opportunity.py"
    page_hold = dash_dir / "pages" / "1_holdings.py"
    page_settings = dash_dir / "pages" / "2_settings.py"
    if not page_opp.is_file():
        _fail(f"dashboard page missing: {page_opp}")
    if not page_hold.is_file():
        _fail(f"dashboard page missing: {page_hold}")
    if not page_settings.is_file():
        _fail(f"dashboard page missing: {page_settings}")

    build_dir = Path("build/gp_assistant")
    warn_file = build_dir / "warn-gp_assistant.txt"
    if warn_file.is_file():
        warn_text = warn_file.read_text(encoding="utf-8", errors="ignore")
        bad = re.findall(
            r"Hidden import 'quant_trading_system[^']*' not found",
            warn_text,
        )
        if bad:
            _fail(
                "PyInstaller could not resolve quant_trading_system modules "
                f"({len(bad)} errors). Use pip install . without -e. "
                f"Example: {bad[0]}"
            )

    xref = build_dir / "xref-gp_assistant.html"
    if xref.is_file():
        xref_text = xref.read_text(encoding="utf-8", errors="ignore")
        for mod in (
            "quant_trading_system.stock_analysis.scheduler",
            "quant_trading_system.stock_analysis.data_fetcher",
            "akshare",
            "certifi",
        ):
            if mod not in xref_text:
                _fail(f"xref missing bundled module: {mod}")
    else:
        _warn("xref-gp_assistant.html not found; skipped xref module check")

    _ok(f"bundle at {root}")
    _ok("no quant_trading_system hidden-import-not-found warnings")
    print("VERIFY PASS")


if __name__ == "__main__":
    main()
