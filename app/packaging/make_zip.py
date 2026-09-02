#!/usr/bin/env python3
"""用 POSIX 路径写 zip（PowerShell Compress-Archive 会写入反斜杠，解压后结构损坏）。"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def add_path(zf: zipfile.ZipFile, src: Path, arcname: Path) -> None:
    if src.is_dir():
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, (arcname / p.relative_to(src)).as_posix())
    elif src.is_file():
        zf.write(src, arcname.as_posix())
    else:
        raise FileNotFoundError(src)


def make_zip(zip_path: Path, sources: list[Path]) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for src in sources:
            src = src.resolve()
            add_path(zf, src, Path(src.name))
    return zip_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path")
    ap.add_argument("sources", nargs="+", help="放到 zip 根目录的文件或文件夹")
    args = ap.parse_args()
    out = make_zip(Path(args.zip_path), [Path(s) for s in args.sources])
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
