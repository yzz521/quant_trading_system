"""单一入口：一个端口、全部功能（Streamlit multipage）。

    streamlit run dashboard/main.py --server.port 8502
    python deploy/ctl.py start-all
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(page_title="量化交易系统", layout="wide", initial_sidebar_state="expanded")

st.title("量化交易系统")
st.markdown(
    """
左侧选择功能页（**只需一个端口**）：

| 页面 | 内容 |
|------|------|
| **持仓与卖出** | 持仓维护、卖出区间 / 深套分批 |
| **个股诊断** | 单票诊断、条件扫描 |
| **研究工具** | 快速回测、持仓风险诊断 |

```text
python deploy/ctl.py start-all
→ http://localhost:8502
```
"""
)
