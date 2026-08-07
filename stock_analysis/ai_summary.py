"""把已有分析结果交给大模型，生成中文点评（不发明买卖指令）。"""
from __future__ import annotations

import json
from typing import Any, Optional

from .ai_client import cfg_from_notify, chat_completion
from ..utils import get_logger

log = get_logger("AISummary")

_SYSTEM = """你是「GP助手」的风控与持仓解说员，不是荐股机器人。
规则：
1. 只根据用户提供的 JSON 事实做归纳，不要编造价格、新闻或未给出的指标。
2. 不要给出「一定买入/卖出某只」的确定性承诺；用「可考虑 / 需观察 / 注意」。
3. 优先谈：持仓风险、卖出/减仓区间、资金是否允许新开仓、满仓时的纪律。
4. 输出简体中文，分 3～6 条短要点，总长控制在 400 字内。
5. 开头一句总括今日状态（如：满仓/有余力/深套为主）。
"""


def _pack_context(
    *,
    market: str,
    holdings: Optional[list] = None,
    holdings_summary: Optional[dict] = None,
    holding_actions: Optional[list] = None,
    capital_snapshot: Optional[dict] = None,
    diagnoses: Optional[list] = None,
    scan_hits: Optional[list] = None,
) -> dict[str, Any]:
    actions_slim = []
    for a in (holding_actions or [])[:20]:
        if not isinstance(a, dict):
            continue
        actions_slim.append({
            "code": a.get("code"),
            "name": a.get("name"),
            "action": a.get("action") or a.get("建议") or a.get("label"),
            "note": a.get("note") or a.get("reason") or a.get("summary"),
            "pnl_pct": a.get("pnl_pct"),
        })
    diag_slim = []
    for d in (diagnoses or [])[:15]:
        if not isinstance(d, dict):
            continue
        diag_slim.append({
            "code": d.get("code"),
            "name": d.get("name"),
            "rating": d.get("rating"),
            "score": d.get("score"),
            "buy_label": d.get("buy_label"),
        })
    scan_slim = []
    for h in (scan_hits or [])[:12]:
        if not isinstance(h, dict):
            continue
        scan_slim.append({
            "code": h.get("code"),
            "name": h.get("name"),
            "score": h.get("score"),
            "buy_label": h.get("buy_label"),
            "matched": h.get("matched"),
        })
    holds = []
    for h in (holdings or [])[:20]:
        if not isinstance(h, dict):
            continue
        holds.append({
            "code": h.get("code"),
            "name": h.get("name"),
            "quantity": h.get("quantity"),
            "cost_price": h.get("cost_price"),
            "current_price": h.get("current_price"),
            "pnl_pct": h.get("pnl_pct"),
        })
    return {
        "market": market,
        "capital": capital_snapshot,
        "holdings_summary": holdings_summary,
        "holdings": holds,
        "holding_actions": actions_slim,
        "diagnoses": diag_slim,
        "scan_hits": scan_slim,
    }


def generate_market_summary(
    notify_cfg: dict[str, Any],
    *,
    market: str,
    holdings: Optional[list] = None,
    holdings_summary: Optional[dict] = None,
    holding_actions: Optional[list] = None,
    capital_snapshot: Optional[dict] = None,
    diagnoses: Optional[list] = None,
    scan_hits: Optional[list] = None,
) -> Optional[str]:
    """Return markdown/plain summary or None if disabled/failed."""
    cfg = cfg_from_notify(notify_cfg)
    if not cfg["enabled"]:
        return None
    if not cfg["api_key"]:
        log.info("AI 已启用但未配置 api_key，跳过")
        return None

    ctx = _pack_context(
        market=market,
        holdings=holdings,
        holdings_summary=holdings_summary,
        holding_actions=holding_actions,
        capital_snapshot=capital_snapshot,
        diagnoses=diagnoses,
        scan_hits=scan_hits,
    )
    user = (
        "请根据以下 JSON（系统已算好的事实）写今日 GP助手 点评：\n"
        + json.dumps(ctx, ensure_ascii=False, indent=2)
    )
    text = chat_completion(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=cfg["model"],
        timeout=cfg["timeout"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
    )
    if text:
        log.info("[%s] AI 点评生成成功，%d 字", market, len(text))
    return text
