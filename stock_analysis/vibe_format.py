"""把 Vibe 返回的分析正文整理成可展示的终稿骨架。"""
from __future__ import annotations

import re
from typing import Any

# Vibe 按 prompt 输出的小节标题，兼容 ①②③④ 与 一、二、三、四 两种序号写法
_HEADINGS = {
    "overview": (r"①\s*一段总括", r"[一二1]\s*[、.．)）]?\s*总括", r"\*\*总括\*\*"),
    "detail": (r"②\s*按标的分条", r"[二2]\s*[、.．)）]?\s*按标的分条", r"按标的分条"),
    "discipline": (r"[三3]\s*条纪律提醒", r"[三3]\s*[、.．)）]?\s*(?:条)?纪律提醒"),
    "disclaimer": (r"④\s*免责声明", r"[四4]\s*[、.．)）]?\s*免责声明"),
}


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


def _section(text: str, pats: tuple[str, ...]) -> str:
    """定位小节标题（如 ## ① 一段总括），返回其正文直到下一个 ## 标题或文末。"""
    for pat in pats:
        m = re.search(r"(?m)^\s*#{0,3}\s*" + pat, text)
        if m:
            rest = text[m.end():]
            nxt = re.search(r"(?m)^\s*#{1,3}\s", rest)
            return rest[: nxt.start() if nxt else len(rest)].strip()
    return ""


def _first_para(section: str, max_len: int = 320) -> str:
    for para in re.split(r"\n\s*\n", section):
        line = re.sub(r"[*_`>#]", "", para).strip()
        if line:
            return line[:max_len] + ("…" if len(line) > max_len else "")
    return ""


def extract_discipline_lines(text: str) -> list[str]:
    block = _section(text, _HEADINGS["discipline"])
    if not block:  # 兼容旧格式：**三条纪律提醒：...**
        m = re.search(
            r"[三3]\s*条纪律提醒[：:]?\s*(?:\*\*)?(.*?)(?:\n\s*4\.|\n##|\n\*\*声明|\Z)",
            text, re.S,
        )
        if m:
            block = m.group(1)
    lines: list[str] = []
    for m in re.finditer(r"(?m)^\s*(?:\d+[.、)．]|[①②③④])\s*(.+)", block):
        s = re.sub(r"[*_`]", "", m.group(1)).strip()
        if len(s) > 6:
            lines.append(s)
    return lines[:5]


def extract_risk_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    detail = _section(text, _HEADINGS["detail"])
    # “- 风险：...” 或 “- 风险（可买性，派生计算）：...” 条目
    for m in re.finditer(
        r"(?m)^\s*[-*]\s*\*{0,2}\s*风险[（(]?[^）):：]*[)）]?[：:]\s*(.+)",
        detail,
    ):
        s = m.group(1).strip()
        if len(s) > 8:
            bullets.append(s)
    # 兼容旧格式
    m = re.search(r"关键风险点已识别：([^\n]+)", text)
    if m:
        for part in re.split(r"[；;]|[①②③④]", m.group(1)):
            p = part.strip().strip("、").strip()
            if len(p) > 8:
                bullets.append(p)
    seen: set[str] = set()
    out: list[str] = []
    for b in bullets:
        key = b[:28]
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out[:6]


def extract_symbol_rows(text: str) -> list[dict[str, str]]:
    """解析持仓 markdown 表（代码|名称|数量|成本价|现价|pnl_pct|…），跳过候选表。"""
    rows: list[dict[str, str]] = []
    for m in re.finditer(
        r"(?m)^\|\s*(\d{6})\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*"
        r"([0-9.]+)\s*\|\s*([0-9.]+)\s*\|\s*([+\-]?[0-9.]+%)\s*\|",
        text,
    ):
        code, name, qty, cost, close, pnl = m.groups()
        rows.append({
            "code": code,
            "name": name.strip(),
            "qty": qty,
            "cost": cost,
            "close": close,
            "pnl": pnl,
            "pnl_amt": "",
        })
    return rows


def _extract_overview(text: str) -> str:
    sec = _section(text, _HEADINGS["overview"])
    if sec:
        return _first_para(sec)
    for pat in (r"\*\*总括\*\*[：:]?\s*([^\n]+)", r"分析立场：([^\n]+)"):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ""


def build_display_summary(raw_summary: str) -> dict[str, Any]:
    text = (raw_summary or "").strip()
    partial = is_process_draft(text)
    risks = extract_risk_bullets(text)
    disciplines = extract_discipline_lines(text)
    symbols = extract_symbol_rows(text)
    overview = _extract_overview(text)

    good = bool(overview) and len(overview) >= 10 and (
        bool(disciplines) or bool(symbols) or len(risks) >= 2
    )
    if not good and not partial and len(text) >= 120:
        # 结构化抽取不可用时直接展示原文，避免只剩碎片/占位
        clean = re.sub(r"\n{3,}", "\n\n", text)
        return {
            "partial": partial,
            "overview": overview,
            "risks": risks,
            "disciplines": disciplines,
            "symbols": symbols,
            "clean_summary": clean[:4000],
            "raw_summary": text,
            "fallback_raw": True,
        }

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
                f"pnl={s.get('pnl', '')} 盈亏额={s.get('pnl_amt') or '—'}"
            )
    if disciplines:
        clean_lines.append("【纪律提醒】")
        clean_lines.extend(f"{i}. {d}" for i, d in enumerate(disciplines, 1))
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
        "fallback_raw": False,
    }
