#!/usr/bin/env python3
"""Build a UTF-8 source zip for Windows local packaging.

macOS `zip` often omits the UTF-8 language-encoding bit; Windows Explorer then
decodes Chinese filenames as GBK (首页.py → 棣栭〉.py). Python zipfile sets the
flag so names survive. Dashboard scripts are ASCII (`home.py`) as a second belt.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    ".venv-win",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".reasonix",
    "results",
    "output",
    "quant_trading_system.egg-info",
}

SKIP_FILE_SUFFIXES = {".db", ".db-journal", ".db-wal", ".db-shm"}
SKIP_FILE_NAMES = {
    "notify.yaml",
    "holdings.yaml",
    "users.yaml",
    "secret.local.yaml",
    ".DS_Store",
    "GP-Assistant-Windows.zip",
}


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.endswith(".egg-info")


def _skip_file(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES:
        return True
    if path.suffix in SKIP_FILE_SUFFIXES:
        return True
    if path.name.startswith("GP-Assistant-build-on-windows-") and path.suffix == ".zip":
        return True
    return False


def make_source_zip(repo: Path, out: Path) -> Path:
    repo = repo.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in sorted(repo.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(repo)
            if any(_skip_dir(p) for p in rel.parts[:-1]):
                continue
            if rel.parts[:2] == ("dist", "GPAssistant"):
                continue
            if _skip_file(path):
                continue
            zf.write(path, rel.as_posix())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    out = make_source_zip(Path(args.repo), Path(args.zip_path))
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
