"""周报统一入口：收集标的 → 调 vendored 脚本生成 PDF → 返回结果。

数据源：当前持仓 + results/latest_funnel.json（漏斗 Top N，缺失时回退
results/latest_scan.json 前 N 只扫描命中）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
WEEKLY_DIR = RESULTS_DIR / "weekly"

try:
    from zoneinfo import ZoneInfo
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING = timezone(timedelta(hours=8))


def _now_beijing() -> datetime:
    return datetime.now(BEIJING)


def _homebrew_lib() -> str:
    """macOS Homebrew 库目录（weasyprint 需要 glib/pango 在 dlopen 路径里）。"""
    for p in ("/opt/homebrew/lib", "/usr/local/lib"):
        if Path(p).exists():
            return p
    return ""


def load_funnel_top(root: Path = ROOT, limit: int = 10) -> list[dict]:
    path = root / "results" / "latest_funnel.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (data.get("hits") or [])[: max(int(limit), 1)]
        except Exception:  # noqa: BLE001
            pass
    path = root / "results" / "latest_scan.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (data.get("hits") or [])[: max(int(limit), 1)]
        except Exception:  # noqa: BLE001
            pass
    return []


def collect_codes(
    holdings: list[dict],
    root: Path = ROOT,
    top_n: int = 10,
) -> list[str]:
    """持仓 + 漏斗 TopN 合并去重，返回 6 位股票代码列表。"""
    codes: list[str] = []
    seen: set[str] = set()
    for h in holdings or []:
        c = str(h.get("code") or "").strip().zfill(6)
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    for hit in load_funnel_top(root, top_n):
        c = str(hit.get("code") or "").strip().zfill(6)
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes


def run_weekly_report(
    root: Path = ROOT,
    *,
    stocks: Optional[list[str]] = None,
    holdings: Optional[list[dict]] = None,
    top_n: int = 10,
    date: Optional[str] = None,
    author: str = "GP助手",
    skip_breadth: bool = True,
    timeout: int = 900,
) -> Path:
    """生成周报 PDF 并返回路径；失败抛 RuntimeError。"""
    codes = stocks or collect_codes(holdings or [], root=root, top_n=top_n)
    if not codes:
        raise RuntimeError("周报标的为空：无持仓且无漏斗/扫描结果")
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    report_date = date or _now_beijing().strftime("%Y-%m-%d")
    out = WEEKLY_DIR / f"周报_{report_date}.pdf"

    script = ROOT / "weekly_report" / "generate_weekly_report.py"
    cmd = [
        sys.executable, str(script),
        "--stocks", ",".join(codes),
        "--author", author,
        "--date", report_date,
        "--output", str(out),
    ]
    if skip_breadth:
        cmd.append("--skip-breadth")
    env = dict(os.environ)
    hb = _homebrew_lib()
    if hb:
        env["DYLD_LIBRARY_PATH"] = hb + (
            ":" + env["DYLD_LIBRARY_PATH"] if env.get("DYLD_LIBRARY_PATH") else ""
        )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(f"周报生成失败 (exit {proc.returncode}): {tail}")
    if not out.exists():
        raise RuntimeError(f"周报生成失败：未找到输出 {out}")
    return out


def report_email(report_path: Path, codes: list[str]) -> tuple[str, str, str]:
    """周报邮件正文：(title, text, html)。"""
    title = f"GP助手 · A股周报 {report_path.stem.split('_')[-1]}"
    text = (
        f"周报已生成（附件 PDF）：{report_path.name}\n"
        f"覆盖 {len(codes)} 只：{'、'.join(codes)}\n"
        f"本地存档：{report_path}\n"
    )
    html = (
        "<p>周报已生成（附件 PDF）：<b>%s</b></p>"
        "<p>覆盖 %d 只：%s</p>"
        "<p style='color:#6b7280;font-size:12px'>本地存档：%s</p>"
    ) % (report_path.name, len(codes), "、".join(codes), report_path)
    return title, text, html
