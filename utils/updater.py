"""Check GitHub Releases and replace a frozen GP Assistant install.

Windows onedir: download GP-Assistant-Windows.zip, kill the running exe,
copy files except config/ and results/, then relaunch.

User data next to the exe is never overwritten.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .app_meta import APP_VERSION, GITHUB_RELEASES_API

ProgressCb = Optional[Callable[[float, str], None]]


@dataclass
class UpdateInfo:
    current: str
    latest: str
    newer: bool
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    html_url: str


def parse_version(tag: str) -> tuple[int, ...]:
    s = str(tag).strip()
    if s.lower().startswith("v"):
        s = s[1:]
    parts: list[int] = []
    for bit in s.split("."):
        num = ""
        for ch in bit:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            parts.append(int(num))
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def expected_asset_name() -> str:
    if sys.platform == "win32":
        return "GP-Assistant-Windows.zip"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            return "GP-Assistant-macOS-arm64.zip"
        return "GP-Assistant-macOS-x64.zip"
    return "GP-Assistant-Linux.tar.gz"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Directory that owns the running executable (Windows onedir / Linux)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """Replaceable install root: .app on macOS, otherwise install_dir()."""
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin" and exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        return exe.parent.parent.parent
    return exe.parent if is_frozen() else install_dir()


def check_latest(timeout: int = 20) -> UpdateInfo:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"GPAssistant/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub 返回 HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"检查更新失败: {e}") from e
    return parse_release(data, current=APP_VERSION)


def parse_release(data: dict[str, Any], current: str = APP_VERSION) -> UpdateInfo:
    tag = str(data.get("tag_name") or "")
    assets = data.get("assets") or []
    want = expected_asset_name()
    url, size = "", 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if name == want:
            url = str(asset.get("browser_download_url") or "")
            size = int(asset.get("size") or 0)
            break
    return UpdateInfo(
        current=current,
        latest=tag,
        newer=bool(tag) and is_newer(tag, current),
        notes=str(data.get("body") or "").strip(),
        asset_name=want,
        asset_url=url,
        asset_size=size,
        html_url=str(data.get("html_url") or ""),
    )


def download_asset(url: str, dest: Path, progress: ProgressCb = None, timeout: int = 120) -> Path:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"GPAssistant/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if progress:
                pct = (got / total) if total else 0.0
                progress(min(pct, 0.99), f"已下载 {got // 1024} KB")
    if progress:
        progress(1.0, "下载完成")
    return dest


def _payload_dir(extracted: Path) -> Path:
    for name in ("GPAssistant", "GP助手"):
        p = extracted / name
        if p.is_dir():
            return p
    apps = sorted(extracted.glob("*.app"))
    if apps:
        return apps[0]
    return extracted


def extract_archive(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        import tarfile

        with tarfile.open(archive) as tf:
            tf.extractall(dest)
    return _payload_dir(dest)


def write_windows_updater(src: Path, dst: Path, bat_path: Path) -> Path:
    exe_name = Path(sys.executable).name if is_frozen() else "GPAssistant.exe"
    src_s = str(src)
    dst_s = str(dst)
    bat_path.write_text(
        "\r\n".join(
            [
                "@echo off",
                "setlocal",
                "timeout /t 2 /nobreak >nul",
                f'taskkill /F /IM "{exe_name}" >nul 2>&1',
                "timeout /t 2 /nobreak >nul",
                f'robocopy "{src_s}" "{dst_s}" /E /XD config results /NFL /NDL /NJH /NJS /nc /ns /np',
                f'start "" "{dst_s}\\{exe_name}"',
                'del "%~f0"',
                "",
            ]
        ),
        encoding="ascii",
        errors="replace",
    )
    return bat_path


def launch_windows_updater(bat_path: Path) -> None:
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        cwd=str(bat_path.parent),
        close_fds=True,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_and_restart(info: UpdateInfo, progress: ProgressCb = None) -> None:
    """Download the release asset and restart into the new files. Frozen only."""
    if not is_frozen():
        raise RuntimeError("开发模式请用 git pull，无需应用内更新")
    if not info.asset_url:
        raise RuntimeError(f"该版本没有 {info.asset_name}，请到 GitHub Releases 手动下载")
    work = Path(tempfile.mkdtemp(prefix="gp-upd-"))
    archive = work / info.asset_name
    if progress:
        progress(0.02, "开始下载")
    download_asset(info.asset_url, archive, progress=progress)
    if progress:
        progress(0.85, "正在解压")
    extracted = extract_archive(archive, work / "unpacked")
    dst = bundle_root()
    if sys.platform == "win32":
        bat = work / "gp_apply_update.bat"
        write_windows_updater(extracted, dst, bat)
        launch_windows_updater(bat)
        time.sleep(0.4)
        os._exit(0)
    raise RuntimeError("当前系统请到 GitHub Releases 下载新包覆盖安装（config/results 请先备份）")
