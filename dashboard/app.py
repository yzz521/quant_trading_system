"""Streamlit dashboard for the quantitative trading system.

Run with::

    streamlit run quant_trading_system/dashboard/app.py

The dashboard lets you:
  * pick a data source (synthetic / AkShare A-shares / yfinance US);
  * pick a strategy and tune its parameters;
  * run a backtest inline and inspect the equity curve, drawdown, metrics,
    open positions and trade log.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run directly via `streamlit run`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
import streamlit as st

from quant_trading_system.analytics import PerformanceReport, compute_metrics
from quant_trading_system.backtest import BacktestConfig, BacktestEngine
from quant_trading_system.data import AkShareSource, SyntheticDataSource, YFinanceSource, BarFeed, AssetClass
from quant_trading_system.strategy import (
    BollingerBandStrategy,
    MovingAverageCrossStrategy,
    MultiFactorStrategy,
    MLStrategy,
    TurtleBreakoutStrategy,
)

st.set_page_config(page_title="量化交易系统 · 回测看板", layout="wide")
st.title("📊 量化交易系统 · 回测看板")


# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_data(source: str, symbols: list[str], start: str, end: str):
    frames = {}
    for sym in symbols:
        if source == "合成数据 (Synthetic)":
            ds = SyntheticDataSource(seed=hash(sym) % 1000)
        elif source == "A股 (AkShare)":
            ds = AkShareSource(AssetClass.EQUITY_CN)
        elif source == "美股 (yfinance)":
            ds = YFinanceSource(AssetClass.EQUITY_US)
        else:
            ds = SyntheticDataSource()
        try:
            df = ds.get_history(sym, start, end)
        except Exception as e:  # noqa: BLE001
            st.warning(f"获取 {sym} 数据失败: {e}")
            continue
        if not df.empty:
            frames[sym] = df
    return frames


def run_backtest(frames, strategy_name, params, capital, weight):
    feed = BarFeed(frames)
    cfg = BacktestConfig(initial_capital=capital, position_weight=weight)
    engine = BacktestEngine(cfg)
    symbols = list(frames.keys())
    if strategy_name == "双均线趋势":
        strat = MovingAverageCrossStrategy(symbols, **params)
    elif strategy_name == "海龟突破":
        strat = TurtleBreakoutStrategy(symbols, **params)
    elif strategy_name == "布林带均值回归":
        strat = BollingerBandStrategy(symbols, **params)
    elif strategy_name == "多因子选股":
        strat = MultiFactorStrategy(symbols, **params)
    elif strategy_name == "机器学习 (RF)":
        strat = MLStrategy(symbols, **params)
    else:
        strat = MovingAverageCrossStrategy(symbols, **params)
    engine.add_strategy(strat)
    portfolio = engine.run(feed)
    return portfolio, engine


# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ 配置")
    source = st.selectbox("数据源", ["合成数据 (Synthetic)", "A股 (AkShare)", "美股 (yfinance)"])
    symbols_raw = st.text_input("标的 (逗号分隔)", "600000,000001,600036")
    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]
    col1, col2 = st.columns(2)
    start = col1.text_input("开始日期", "2022-01-01")
    end = col2.text_input("结束日期", "2024-12-31")
    capital = st.number_input("初始资金", 100_000, 10_000_000, 1_000_000, step=100_000)
    weight = st.slider("单标的仓位权重", 0.02, 0.5, 0.10, 0.01)

    strategy_name = st.selectbox(
        "策略", ["双均线趋势", "海龟突破", "布林带均值回归", "多因子选股", "机器学习 (RF)"]
    )
    params = {}
    if strategy_name == "双均线趋势":
        params["fast"] = st.slider("快线", 2, 30, 5)
        params["slow"] = st.slider("慢线", 10, 120, 20)
    elif strategy_name == "海龟突破":
        params["entry"] = st.slider("突破周期", 5, 60, 20)
        params["exit"] = st.slider("离场周期", 3, 30, 10)
    elif strategy_name == "布林带均值回归":
        params["window"] = st.slider("窗口", 5, 60, 20)
        params["num_std"] = st.slider("标准差倍数", 1.0, 3.0, 2.0, 0.1)
    elif strategy_name == "多因子选股":
        params["rebalance_days"] = st.slider("调仓周期(天)", 1, 20, 5)
        params["top_n"] = st.slider("持仓数", 1, 10, 3)
    elif strategy_name == "机器学习 (RF)":
        params["train_size"] = st.slider("训练样本数", 100, 500, 200)
        params["retrain_every"] = st.slider("重训频率(bar)", 20, 200, 50)

    run_btn = st.button("🚀 运行回测", type="primary", use_container_width=True)

if run_btn or "portfolio" not in st.session_state:
    if not symbols:
        st.error("请输入至少一个标的")
        st.stop()
    with st.spinner("加载数据 & 运行回测中..."):
        frames = load_data(source, symbols, start, end)
        if not frames:
            st.error("未能获取任何数据，请检查标的代码或切换数据源")
            st.stop()
        portfolio, engine = run_backtest(frames, strategy_name, params, capital, weight)
        st.session_state["portfolio"] = portfolio
        st.session_state["frames"] = frames
        st.session_state["strategy"] = strategy_name

portfolio = st.session_state.get("portfolio")
if portfolio is None:
    st.info("在左侧配置后点击 **运行回测** 开始。")
    st.stop()

# --------------------------------------------------------------------------- #
metrics = compute_metrics(portfolio)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("最终权益", f"{metrics.get('final_equity', 0):,.0f}")
c2.metric("总收益", f"{metrics.get('total_return', 0)*100:.2f}%")
c3.metric("夏普", f"{metrics.get('sharpe', 0):.2f}")
c4.metric("最大回撤", f"{metrics.get('max_drawdown', 0)*100:.2f}%")
c5.metric("交易次数", f"{metrics.get('n_trades', 0)}")

st.divider()
eq = portfolio.equity_curve_frame()
if not eq.empty:
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.subheader("净值曲线")
        st.line_chart(eq["equity"])
    with col_b:
        st.subheader("回撤")
        dd = eq["equity"] / eq["equity"].cummax() - 1
        st.area_chart(dd)
    st.subheader("月度收益 (%)")
    monthly = eq["return"].resample("ME").apply(lambda x: (1 + x).prod() - 1) * 100
    st.bar_chart(monthly)

st.divider()
left, right = st.columns(2)
with left:
    st.subheader("持仓明细")
    pos_df = portfolio.positions_frame()
    st.dataframe(pos_df if not pos_df.empty else pd.DataFrame(["无持仓"]), use_container_width=True)
with right:
    st.subheader("绩效指标")
    m_df = pd.DataFrame(
        [{"指标": k, "值": v} for k, v in metrics.items()]
    )
    st.dataframe(m_df, use_container_width=True, hide_index=True)

st.subheader("近期成交")
if portfolio.trades:
    st.dataframe(pd.DataFrame(portfolio.trades[-30]), use_container_width=True)
else:
    st.write("无成交记录")
