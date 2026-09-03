# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(
    page_title="GP助手 · 每日决策",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.ui_theme import apply_theme
from quant_trading_system.utils.app_meta import APP_VERSION

apply_theme()
require_login("GP助手 · 每日决策")

st.markdown(
    f"""
<div class="qts-hero">
  <div class="brand">GP ASSISTANT · DAILY DECISION · v{APP_VERSION}</div>
  <h1 style="margin:0;color:#e8f1ff">◈ GP助手 · 每日决策</h1>
  <p style="margin:0.35rem 0 0;color:#8b9bb8">今日机会 · 我的持仓 · 配置</p>
</div>
""",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown('<div class="qts-card"><div class="badge">DAILY</div><h3>今日机会</h3><p>市场状态 + 交易计划（评分/入场区间/止损/目标/风险收益比/仓位）+ AI 解读 + 历史回测 + 批量扫描。</p></div>', unsafe_allow_html=True)
with c2:
    st.markdown('<div class="qts-card"><div class="badge">HOLDINGS</div><h3>持仓与卖出区间</h3><p>维护持仓、卖出/加仓参考、资金账户、粘贴成交同步。</p></div>', unsafe_allow_html=True)
with c3:
    st.markdown('<div class="qts-card"><div class="badge">SETTINGS</div><h3>配置</h3><p>是否发邮件、收件地址、监测 A股/港股/美股、扫描与调度参数。</p></div>', unsafe_allow_html=True)

st.caption("请从左侧进入功能页（opportunity / holdings / settings）。应用更新在 settings。")
