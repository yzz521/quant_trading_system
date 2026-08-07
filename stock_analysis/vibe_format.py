"""把 Vibe 返回的过程稿整理成可展示的终稿骨架。"""
from __future__ import annotations

import re
from typing import Any


def is_process_draft(text: str) -> bool:
    if not text:
        return True
    markers = (
        "## Goal",
        "## Progress",
        "## Remaining Work",
        "尚未输出",
        "最终点评文本",
        "In Progress",
        "Pending User Asks",
    )
    return sum(1 for m in markers if m in text) >= 2


def extract_discipline_lines(text: str) -> list[str]:
    lines: list[str] = []
    block = re.search(
        r"3\s*条纪律提醒[：:]?\s*(?:\*\*)?(.*?)(?:\n\s*4\.|\n##|\n\*\*声明|\Z)",
        text,
        re.S,
    )
    if block:
        for m in re.finditer(r"[①②③]\s*([^①②③\n]+)", block.group(1)):
            s = m.group(1).strip().strip("*").strip()
            if len(s) > 6:
                lines.append(s)
    if not lines:
        for pat in (
            r"先降集中度[^。\n]{0,40}",
            r"满仓下任何新买入[^。\n]{0,50}",
            r"用金额[^。\n]{0,60}",
        ):
            m = re.search(pat, text)
            if m:
                lines.append(m.group(0).strip())
    seen: set[str] = set()
    out: list[str] = []
    for x in lines:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:5]


def extract_risk_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    m = re.search(r"关键风险点已识别：([^\n]+)", text)
    if m:
        chunk = m.group(1)
        for part in re.split(r"[；;]|[①②③④]", chunk):
            p = part.strip().strip("、").strip()
            if len(p) > 8:
                bullets.append(p)
    for pat in (
        r"招行[^，。\n]{0,10}(?:超|超过)[^，。\n]{0,30}",
        r"银行[^，。\n]{0,25}77\.6%[^，。\n]{0,25}",
        r"推算浮亏[^，。\n]{0,30}",
        r"无深套[^，。\n]{0,25}",
    ):
        m = re.search(pat, text)
        if m:
            bullets.append(m.group(0).strip())
    seen: set[str] = set()
    out: list[str] = []
    for b in bullets:
        key = b[:28]
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out[:6]


def extract_symbol_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for m in re.finditer(
        r"(?P<code>\d{6})\s+(?P<name>[\u4e00-\u9fffA-Za-z0-9]+)\s+"
        r"(?P<qty>\d+)/(?P<cost>[0-9.]+)/(?P<cost_amt>[0-9.]+)/"
        r"(?P<pnl>[+\-0-9.]+%)/(?P<pnl_amt>[+\-0-9.]+)",
        text,
    ):
        rows.append({k: m.group(k) for k in m.groupdict()})
    return rows


def build_display_summary(raw_summary: str) -> dict[str, Any]:
    text = raw_summary or ""
    partial = is_process_draft(text)
    risks = extract_risk_bullets(text)
    disciplines = extract_discipline_lines(text)
    symbols = extract_symbol_rows(text)

    overview = ""
    m = re.search(r"\*\*总括\*\*[：:]?\s*([^\n]+)", text)
    if m:
        overview = m.group(1).strip()
    if not overview:
        m = re.search(r"分析立场：([^\n]+)", text)
        if m:
            overview = m.group(1).strip()
    if not overview and risks:
        overview = "；".join(risks[:3])

    clean_lines: list[str] = []
    if overview:
        clean_lines.append("【总括】" + overview)
    if risks:
        clean_lines.append("【风险要点】")
        clean_lines.extend(f"· {r}" for r in risks)
    if symbols:
        clean_lines.append("【标的速览】")
        for s in symbols:
            clean_lines.append(
                f"· {s.get('code', '')} {s.get('name', '')} "
                f"pnl={s.get('pnl', '')} 盈亏额={s.get('pnl_amt', '')}"
            )
    if disciplines:
        clean_lines.append("【纪律提醒】")
        for i, d in enumerate(disciplines, 1):
            clean_lines.append(f"{i}. {d}")
    clean_lines.append(
        "【声明】仅研究参考，非投资建议；推算值基于账本字段，未校验实时行情。"
    )
    return {
        "partial": partial,
        "overview": overview,
        "risks": risks,
        "disciplines": disciplines,
        "symbols": symbols,
        "clean_summary": "\n".join(clean_lines),
        "raw_summary": text,
    }
