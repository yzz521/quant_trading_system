"""Stock analysis dashboard — diagnose any stock or scan the market interactively.

Run::

    streamlit run quant_trading_system/dashboard/stock_app.py

Two tabs:
  * 个股诊断: enter a code (600000 / AAPL / 00700) → score, rating, K-line,
             indicators, signals, risks.
  * 选股扫描: pick conditions + a stock list → ranked hits.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import streamlit as st

from quant_trading_system.stock_analysis import (
    StockDiagnoser, StockScanner, detect_market, fetch_kline,
    add_all_indicators, PRESETS,
)
from quant_trading_system.stock_analysis.report import plot_kline


from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
from quant_trading_system.dashboard.auth import require_login
apply_theme()
require_login()
page_header("信号扫描", "评分命中 · 可买性过滤", "Scanner")

tab_diag, tab_scan = st.tabs(["🔍 个股诊断", "📋 选股扫描"])

# --------------------------------------------------------------------------- #
with tab_diag:
    col1, col2 = st.columns([1, 4])
    code = col1.text_input("股票代码", "600000",
                           help="A股: 600000 / 000001  ·  美股: AAPL  ·  港股: 00700")
    run = col1.button("开始诊断", type="primary", use_container_width=True)

    if run:
        with st.spinner(f"正在分析 {code} ..."):
            try:
                result = StockDiagnoser().diagnose(code)
                info = detect_market(code)
                df = fetch_kline(info, days=120)
            except Exception as e:
                st.error(f"分析失败: {e}")
                st.stop()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("综合评分", f"{result.score}/100")
        c2.metric("评级", result.rating)
        c3.metric("当前价", f"{result.price}", f"{result.change_pct:+.2f}%")
        c4.metric("趋势", result.trend)

        st.divider()
        st.subheader(f"{result.name}({result.code}) · 诊断结论")
        st.info(result.summary)

        left, right = st.columns([2, 1])
        with left:
            st.subheader("K线与技术指标")
            if not df.empty:
                fig = plot_kline(add_all_indicators(df) if "ma5" not in df.columns else df,
                                 f"{result.name}({result.code})")
                st.pyplot(fig)

        with right:
            st.subheader("形态信号")
            if result.signals:
                sig_df = pd.DataFrame([
                    {"信号": s["name"], "方向": s["type"], "说明": s.get("detail", "")}
                    for s in result.signals
                ])
                st.dataframe(sig_df, use_container_width=True, hide_index=True)
            else:
                st.write("无明显信号")

            st.subheader("风险提示")
            for r in result.risks:
                st.write(f"⚠️ {r}")

        st.divider()
        st.subheader("技术指标快照")
        ind_df = pd.DataFrame([result.indicators]).T.reset_index()
        ind_df.columns = ["指标", "数值"]
        st.dataframe(ind_df, use_container_width=True, hide_index=True)

        if result.fund_flow or result.valuation:
            cF, cV = st.columns(2)
            if result.fund_flow:
                with cF:
                    st.subheader("资金面")
                    st.json(result.fund_flow)
            if result.valuation:
                with cV:
                    st.subheader("估值面")
                    st.json(result.valuation)

# --------------------------------------------------------------------------- #
with tab_scan:
    sc1, sc2 = st.columns([1, 2])
    conds = sc1.multiselect("筛选条件", list(PRESETS.keys()),
                            default=["多头排列", "MACD金叉", "突破新高"])
    pool = sc2.text_area("股票池（逗号分隔）",
                         "600000,000001,600036,601318,000858,600519,601166,002594,300750,000333,600276,000651")
    scan_btn = st.button("开始扫描", type="primary")

    if scan_btn:
        codes = [c.strip() for c in pool.split(",") if c.strip()]
        if not codes or not conds:
            st.warning("请填写股票池和筛选条件")
            st.stop()
        with st.spinner(f"扫描 {len(codes)} 只标的..."):
            hits = StockScanner(max_workers=8).scan(codes, conds, limit=50)
        if not hits:
            st.info("无标的命中所选条件")
        else:
            rows = [h.to_dict() for h in hits]
            df = pd.DataFrame(rows)
            st.dataframe(df[["code", "name", "close", "change_pct", "score", "matched"]],
                         use_container_width=True, hide_index=True)
            st.caption(f"命中 {len(hits)} 只 · 按评分排序")
