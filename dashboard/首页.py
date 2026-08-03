"""应用首页（单一端口入口）。

    streamlit run dashboard/首页.py --server.port 8502
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(page_title="量化交易系统", layout="wide", initial_sidebar_state="expanded")

st.title("量化交易系统 · 首页")
st.markdown(
    """
请从**左侧**进入功能（同一地址，不必开多个端口）：

| 菜单 | 做什么 | 结果用在哪 |
|------|--------|------------|
| **持仓与卖出区间** | 维护持仓、看卖出/止损/深套分批目标 | 日常持仓决策、是否减仓/止损 |
| **个股诊断与扫描** | 单票打分、条件选股 | 找标的；扫描结果可进回测股票池 |
| **研究工具** | 合成数据快速回测、持仓组合风险诊断 | 验证策略思路、看组合是否超限 |

默认打开后点 **「持仓与卖出区间」** 即可回到你最常用的页面。
"""
)
st.info("提示：侧栏「首页」只是导航说明；业务数据都在下面三个菜单里。")
