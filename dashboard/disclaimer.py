"""统一免责声明文案。

本应用会输出评分、入场区间、止损、目标价、仓位与 BUY/SELL 等决策标签。
自用无妨，但一旦把安装包分发给他人，这些输出就带有"荐股"性质，必须显式
声明非投资建议，并标注行情数据的来源归属。

用法：在页面脚本末尾调用 ``render_disclaimer()``。
"""
from __future__ import annotations

import streamlit as st

DISCLAIMER = (
    "本工具仅供个人量化研究使用。所有评分、区间、仓位与决策标签均为模型输出，"
    "不构成任何投资建议，据此操作风险自负。"
    "行情数据来源：同花顺金融数据服务 / 新浪 / 腾讯 / AkShare，"
    "数据版权归各自提供方所有，请勿转售或用于商业分发。"
)


def render_disclaimer() -> None:
    """在看板页面底部渲染免责声明。"""
    st.caption(DISCLAIMER)
