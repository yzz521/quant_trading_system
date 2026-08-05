from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(
    page_title="量化交易系统",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from quant_trading_system.dashboard.ui_theme import apply_theme
from quant_trading_system.dashboard.auth import require_login

apply_theme()
require_login("量化交易系统")

st.markdown(
    """
<div class="qts-hero">
  <div class="brand">QUANT SYS · PERSONAL DESK</div>
  <h1 style="margin:0;color:#e8f1ff">◈ 量化交易系统</h1>
  <p style="margin:0.35rem 0 0;color:#8b9bb8">本地持仓助手 · 卖出区间 · 诊断扫描 · 研究回测</p>
</div>
""",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown('<div class="qts-card"><div class="badge">DAILY</div><h3>持仓与卖出区间</h3><p>维护持仓、记录卖出、资金与可买性、深套分批。</p></div>', unsafe_allow_html=True)
with c2:
    st.markdown('<div class="qts-card"><div class="badge">SCAN</div><h3>个股诊断与扫描</h3><p>单票评分与条件扫描，标注是否买得起。</p></div>', unsafe_allow_html=True)
with c3:
    st.markdown('<div class="qts-card"><div class="badge">LAB</div><h3>研究工具</h3><p>合成回测与组合风险，研究用。</p></div>', unsafe_allow_html=True)

st.caption("请从左侧进入功能页。")
