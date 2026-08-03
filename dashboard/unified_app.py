"""统一入口：回测 / 持仓风险 / 入口说明。

完整持仓 CRUD + 卖出区间仍由 holdings_app 提供（ctl 可切换）。
个股诊断扫描仍由 stock_app 提供。

Run::

    streamlit run quant_trading_system/dashboard/unified_app.py --server.port 8502
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from quant_trading_system.analytics import compute_metrics
from quant_trading_system.backtest import BacktestConfig, BacktestEngine
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.risk_diagnosis import diagnose_holdings
from quant_trading_system.strategy import create_strategy, list_strategies

st.set_page_config(page_title="量化交易系统", layout="wide")
st.title("量化交易系统 · 统一入口")

tab_bt, tab_risk, tab_help = st.tabs(["📊 快速回测", "🛡️ 持仓风险诊断", "📎 入口说明"])

# ---------- 回测 ----------
with tab_bt:
    st.subheader("合成数据快速回测")
    c1, c2, c3 = st.columns(3)
    with c1:
        strat_name = st.selectbox("策略", [s for s in list_strategies() if s in (
            "ma_cross", "turtle", "bollinger", "multi_factor"
        )] or list_strategies()[:4])
    with c2:
        capital = st.number_input("初始资金", value=500_000, step=50_000)
    with c3:
        days = st.slider("合成行情天数", 120, 800, 400)

    if st.button("运行回测", type="primary"):
        ds = SyntheticDataSource(seed=42)
        # approximate date range
        df = ds.get_history("DEMO", "2022-01-01", "2024-12-31")
        if len(df) > days:
            df = df.iloc[-days:]
        feed = BarFeed({"DEMO": df})
        cfg = BacktestConfig(
            initial_capital=float(capital),
            t1_enabled=False,
            enforce_limit=False,
            enforce_volume=False,
            lot_size=1,
        )
        try:
            strategy = create_strategy(strat_name, symbols=["DEMO"], fast=5, slow=20)
        except TypeError:
            strategy = create_strategy(strat_name, symbols=["DEMO"])
        eng = BacktestEngine(cfg)
        eng.add_strategy(strategy)
        pf = eng.run(feed)
        m = compute_metrics(pf)
        st.success("回测完成")
        st.json({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k in (
            "total_return", "sharpe", "max_drawdown", "annual_return", "final_equity", "n_orders"
        )})
        eq = pf.equity_curve_frame()
        if not eq.empty:
            st.line_chart(eq["equity"])

# ---------- 持仓风险 ----------
with tab_risk:
    st.subheader("持仓 → 框架风控快照")
    st.caption("读取本地 config/holdings.db，不产生订单。可调单票权重与总暴露上限。")
    cfg_path = str(Path(__file__).resolve().parents[1] / "config" / "holdings.yaml")
    max_w = st.slider("单票权重上限", 0.05, 0.50, 0.25, 0.05)
    max_e = st.slider("总暴露上限", 0.5, 2.0, 1.0, 0.1)
    capital_r = st.number_input("用于估值的权益基数（现金+市值近似）", value=1_000_000.0, step=50_000.0)

    if st.button("诊断当前持仓", type="primary"):
        try:
            h = Holdings(cfg_path)
            rows = h.all()
            if not rows:
                st.warning("持仓为空")
            else:
                # use cost as proxy price if no live feed
                prices = {r["code"]: float(r.get("cost_price") or 0) for r in rows}
                # try refresh close
                try:
                    from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_kline
                    for r in rows:
                        code = r["code"]
                        try:
                            info = detect_market(code)
                            df = fetch_kline(info, days=5)
                            if df is not None and not df.empty:
                                prices[code] = float(df["close"].iloc[-1])
                        except Exception:
                            pass
                except Exception:
                    pass
                report = diagnose_holdings(
                    rows, prices, capital=float(capital_r),
                    max_position_pct=float(max_w), max_exposure=float(max_e),
                )
                if report["ok"]:
                    st.success("未触发组合层告警")
                else:
                    for a in report["alerts"]:
                        st.error(a)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("权益近似", f"{report['equity']:,.0f}")
                m2.metric("现金", f"{report['cash']:,.0f}")
                m3.metric("总暴露", f"{report['gross_exposure']:.1%}")
                m4.metric("持仓数", report["n_positions"])
                st.dataframe(pd.DataFrame(report["positions"]), use_container_width=True, hide_index=True)
        except Exception as e:
            st.exception(e)

# ---------- 说明 ----------
with tab_help:
    st.markdown("""
### 完整功能入口

| 页面 | 启动方式 |
|------|----------|
| **本页（统一入口）** | `streamlit run dashboard/unified_app.py` 或 `./deploy/ctl.sh dashboard` |
| 持仓 CRUD + 卖出区间 | `streamlit run dashboard/holdings_app.py` 或 `./deploy/ctl.sh holdings` |
| 个股诊断 / 扫描 | `streamlit run dashboard/stock_app.py` 或 `./deploy/ctl.sh stock` |
| 经典回测看板 | `streamlit run dashboard/app.py` |

框架与助手边界见 `docs/architecture.md`。
""")
