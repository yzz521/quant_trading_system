"""Vibe-Trading 二次分析展示页（主项目发起，结果收回）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

try:
    from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
    from quant_trading_system.dashboard.auth import require_login
    apply_theme()
    require_login()
    page_header("Vibe 二次分析", "投喂持仓 → 本地 Vibe → 结构化结果", "Bridge")
except Exception:
    st.set_page_config(page_title="Vibe 二次分析", layout="wide")
    st.title("Vibe 二次分析")

from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.vibe_bridge import (
    build_payload,
    health,
    list_results,
    load_latest_scan,
    load_result,
    submit_secondary_analysis,
    DEFAULT_BASE,
)
from quant_trading_system.stock_analysis.vibe_format import build_display_summary

ROOT = Path(__file__).resolve().parents[2]

st.caption(
    "以 GP助手为事实源。Vibe：`vibe-trading serve --port 8899`"
    "（[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)）。"
    "过程稿会自动抽成摘要卡片。"
)

col_a, col_b = st.columns([2, 1])
with col_b:
    base_url = st.text_input("Vibe API", value=DEFAULT_BASE)
    auth_key = st.text_input("API_AUTH_KEY（本地可空）", value="", type="password")
    online = health(base_url, auth_key)
    if online:
        st.success("Vibe API 可访问")
    else:
        st.warning("未检测到 Vibe")

with col_a:
    st.markdown("##### 发起二次分析")
    run = st.button("发送到 Vibe", type="primary")

# 扫描候选（来自调度器每小时落盘的 results/latest_scan.json）
latest_scan = load_latest_scan(ROOT)
candidates = (latest_scan.get("hits") or [])[:15]
cand_col, btn_col = st.columns([4, 1])
with cand_col:
    if candidates:
        st.caption(
            f"📋 已载入扫描候选 {len(candidates)} 只"
            f"（as_of {latest_scan.get('as_of', '')} · {latest_scan.get('market', '')}）"
        )
    else:
        st.caption("📋 暂无扫描候选：等待调度器生成 results/latest_scan.json（每小时扫描后自动落盘）")
with btn_col:
    if st.button("重新读取候选", key="reload_scan"):
        st.rerun()

holder = Holdings(str(ROOT / "config" / "holdings.yaml"))
positions = holder.all()
capital = holder.capital_snapshot() if hasattr(holder, "capital_snapshot") else None
actions = []
try:
    from quant_trading_system.stock_analysis.holdings_action import analyze_holding_actions
    if positions:
        actions = analyze_holding_actions(positions)
except Exception as e:  # noqa: BLE001
    st.info(f"持仓动作分析跳过: {e}")

payload = build_payload(
    holdings=positions,
    holding_actions=actions,
    capital_snapshot=capital,
    candidates=candidates,
    market="CN",
)
with st.expander("预览投喂 JSON", expanded=False):
    st.code(json.dumps(payload, ensure_ascii=False, indent=2), language="json")

if run:
    with st.spinner("请求 Vibe 并等待回复（可能数分钟）…"):
        result = submit_secondary_analysis(
            payload, root=ROOT, base_url=base_url, auth_key=auth_key,
        )
    st.session_state["vibe_last_result"] = result

def _render_result(result: dict) -> None:
    disp = result.get("display")
    if not disp:
        disp = build_display_summary(result.get("summary") or "")
    if disp.get("partial"):
        st.warning("Vibe 返回的是过程稿；以下为自动抽取的可用摘要（非完整终稿）。")
    elif result.get("ok"):
        st.success("已收到分析")
    else:
        st.warning(result.get("error") or "未完整解析")

    st.markdown("##### 摘要")
    st.markdown(result.get("clean_summary") or disp.get("clean_summary") or "（无）")

    risks = disp.get("risks") or []
    if risks:
        st.markdown("##### 风险要点")
        for r in risks:
            st.markdown(f"- {r}")

    symbols = disp.get("symbols") or []
    if symbols:
        st.markdown("##### 标的速览")
        st.dataframe(symbols, use_container_width=True)

    discs = disp.get("disciplines") or []
    if discs:
        st.markdown("##### 纪律提醒")
        for i, d in enumerate(discs, 1):
            st.markdown(f"{i}. {d}")

    st.caption(
        f"session={result.get('session_id')} · attempt={result.get('attempt_id')} · "
        f"payload={result.get('payload_path')} · result={result.get('result_path')}"
    )
    with st.expander("原始返回（调试）"):
        st.text(result.get("summary") or "")
        st.json({k: result.get(k) for k in ("ok", "partial", "error", "session_id")})


result = st.session_state.get("vibe_last_result")
if result:
    st.divider()
    st.markdown("##### 最新结果")
    _render_result(result)

st.divider()
st.markdown("##### 历史结果")
files = list_results(ROOT, limit=15)
if not files:
    st.caption("暂无历史记录")
else:
    names = [p.name for p in files]
    pick = st.selectbox("选择记录", names)
    data = load_result(files[names.index(pick)])
    _render_result(data)
