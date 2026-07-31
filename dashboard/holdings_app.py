"""持仓管理看板 — 在 Web 界面维护本地 SQLite 数据库中的持仓信息.

Run::

    streamlit run quant_trading_system/dashboard/holdings_app.py

数据存储: ``config/holdings.db``（本地 SQLite，不入库；调度器推送时读取同一数据库）

功能:
  * 持仓总览: 表格展示 + 可选拉取实时行情计算盈亏
  * 新增持仓: 输入代码/名称/市场/成本/数量/买入日期，代码可自动识别市场并查询名称
  * 编辑持仓: 选择一条记录修改字段
  * 删除持仓: 勾选多条批量删除
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_name
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.trade_monitor import TradeMonitor

MARKETS = ["CN", "US", "HK"]

# 兼容旧路径：Holdings 内部会自动切换到同目录的 holdings.db
HOLDINGS_CFG = str(Path(__file__).resolve().parents[1] / "config" / "holdings.yaml")
NOTIFY_CFG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def get_holdings() -> Holdings:
    return Holdings(HOLDINGS_CFG)


def _label(p: dict) -> str:
    return f"{p['code']} {p.get('name', '')}".strip()


# --------------------------------------------------------------------------- #
st.set_page_config(page_title="持仓管理 · 量化交易系统", layout="wide")
st.title("💼 持仓管理")

_holder = get_holdings()
st.caption(f"数据存储：`{_holder.db_path}`（本地 SQLite，不入库；调度器推送时读取同一数据库）")

tab_overview, tab_paste, tab_add, tab_edit, tab_delete = st.tabs(
    ["📊 持仓总览", "📋 粘贴成交", "➕ 新增持仓", "✏️ 编辑持仓", "🗑️ 删除持仓"]
)

# =========================== 持仓总览 =========================== #
with tab_overview:
    positions = _holder.all()
    if not positions:
        st.info("暂无持仓，请到「新增持仓」标签页添加。")
    else:
        show_pnl = st.checkbox("📈 获取实时行情并计算盈亏", value=False,
                               help="勾选后联网获取最新价计算市值与盈亏；网络受限时可关闭")
        if show_pnl:
            with st.spinner("获取实时行情中 ..."):
                try:
                    rows, summary = _holder.compute_pnl()
                    df = pd.DataFrame(rows)
                    cols = ["code", "name", "market", "cost_price", "quantity",
                            "current_price", "market_value", "pnl", "pnl_pct",
                            "hold_days", "buy_date"]
                    df = df[[c for c in cols if c in df.columns]]
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("持仓数", f"{summary['count']}")
                    c2.metric("总成本", f"{summary['total_cost']:,.0f}")
                    c3.metric("总市值", f"{summary['total_value']:,.0f}")
                    c4.metric("总盈亏", f"{summary['total_pnl']:+,.0f}",
                              f"{summary['total_pnl_pct']:+.2f}%")
                except Exception as e:  # noqa: BLE001
                    st.warning(f"实时行情获取失败（{e}），以下仅显示配置信息")
                    df = pd.DataFrame(positions)
        else:
            df = pd.DataFrame(positions)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"共 {len(positions)} 条持仓")

# =========================== 粘贴成交 =========================== #
with tab_paste:
    st.subheader("📋 粘贴成交记录")
    st.caption("把券商的『成交提醒』文本直接粘贴进来，自动识别买卖方向/代码/数量/价格并同步持仓")
    raw = st.text_area(
        "成交文本（支持同花顺成交提醒 / 自由文本）", height=200,
        placeholder=("示例：\n成交提醒\n股票代码：513310\n"
                     "股票名称：中韩半导体ETF华泰柏瑞\n"
                     "交易方向：买入，委托数量200股\n"
                     "成交量：已成交200股，已全部成交\n"
                     "成交金额：937.40元（成交价格：4.687元）"),
        key="paste_trade")

    if st.button("🔍 解析成交", type="primary", use_container_width=True):
        if not raw.strip():
            st.warning("请先粘贴成交文本")
        else:
            try:
                mon = TradeMonitor(NOTIFY_CFG)
                t = mon.parser.parse(raw)
                if t is None:
                    st.error("未能识别出成交信息：需要包含 交易方向（买入/卖出）+ 股票代码")
                else:
                    st.session_state["paste_result"] = t
            except Exception as e:  # noqa: BLE001
                st.error(f"解析失败: {e}")

    result = st.session_state.get("paste_result")
    if result:
        st.markdown("**解析结果：**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("方向", "买入 ▶" if result.side == "BUY" else "卖出 ◀")
        c2.metric("代码", result.code)
        c3.metric("数量", f"{int(result.quantity)} 股" if result.quantity else "未识别")
        c4.metric("价格", f"{result.price:.4f}" if result.price else "未识别")
        st.write(f"名称：{result.name or '（未知）'}")

        if st.button("💾 确认写入持仓", use_container_width=True):
            try:
                mon = TradeMonitor(NOTIFY_CFG)
                action, msg = mon.apply_trade(result)
                _holder.reload()  # 与页面实例同步
                st.success(f"✔ [{action}] {msg}")
                del st.session_state["paste_result"]
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"写入失败: {e}")

# =========================== 新增持仓 =========================== #
with tab_add:
    st.subheader("➕ 新增持仓")
    code = st.text_input("股票代码", key="add_code", placeholder="如 600000 / AAPL / 00700")
    if st.button("🔍 自动识别市场 & 查询名称", use_container_width=True):
        c = code.strip()
        if not c:
            st.warning("请先输入股票代码")
        else:
            try:
                info = detect_market(c)
                st.session_state["add_name"] = fetch_name(info)
                st.session_state["add_market"] = info.market
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"自动识别失败: {e}")

    name = st.text_input("股票名称", key="add_name", placeholder="留空则保存时用代码补全")
    market = st.selectbox("市场", MARKETS, key="add_market")
    col1, col2 = st.columns(2)
    cost_price = col1.number_input("成本价（每股）", min_value=0.0, value=0.0,
                                   step=0.001, format="%.3f")
    quantity = col2.number_input("持有数量（股）", min_value=1, value=100, step=100)
    buy_date = st.text_input("买入日期（YYYY-MM-DD，可留空）", value="")

    if st.button("💾 保存新增", type="primary", use_container_width=True):
        c = code.strip().upper()
        if not c:
            st.error("股票代码不能为空")
        elif cost_price <= 0 or quantity <= 0:
            st.error("成本价和数量必须大于 0")
        else:
            if any(p["code"].upper() == c for p in _holder.all()):
                st.warning(f"{c} 已在持仓中，请到「编辑持仓」修改")
            else:
                _holder.add(c, name.strip() or c, market,
                            cost_price, int(quantity), buy_date.strip())
                st.success(f"已保存：{c} {name.strip() or c}（{market}）")
                st.rerun()

# =========================== 编辑持仓 =========================== #
with tab_edit:
    st.subheader("✏️ 编辑持仓")
    positions = _holder.all()
    if not positions:
        st.info("暂无持仓可编辑")
    else:
        sel = st.selectbox("选择持仓", range(len(positions)),
                           format_func=lambda i: _label(positions[i]), key="edit_sel")
        if sel >= len(positions):  # 防御：持仓被删除后索引越界
            sel = 0
        p = positions[sel]
        old_code = p["code"]
        e1, e2 = st.columns(2)
        e_code = e1.text_input("代码", value=old_code, key=f"edit_code_{sel}")
        e_name = e2.text_input("名称", value=p.get("name", ""), key=f"edit_name_{sel}")
        e_market = st.selectbox("市场", MARKETS,
                                index=MARKETS.index(p.get("market", "CN")),
                                key=f"edit_market_{sel}")
        e3, e4 = st.columns(2)
        e_cost = e3.number_input("成本价（每股）", min_value=0.0,
                                 value=float(p["cost_price"]), step=0.001,
                                 format="%.3f", key=f"edit_cost_{sel}")
        e_qty = e4.number_input("持有数量（股）", min_value=1,
                                value=max(1, int(float(p["quantity"]))),
                                step=100, key=f"edit_qty_{sel}")
        e_date = st.text_input("买入日期（YYYY-MM-DD，可留空）",
                               value=p.get("buy_date", ""), key=f"edit_date_{sel}")

        if st.button("💾 更新", type="primary", use_container_width=True):
            new_code = e_code.strip().upper()
            if not new_code:
                st.error("代码不能为空")
            elif e_cost <= 0 or e_qty <= 0:
                st.error("成本价和数量必须大于 0")
            elif new_code != old_code and any(
                    x["code"].upper() == new_code for x in _holder.all()
                    if x["code"] != old_code):
                st.error(f"{new_code} 已存在于其他持仓")
            else:
                if new_code == old_code:
                    _holder.update(old_code, name=e_name.strip() or new_code,
                                   market=e_market, cost_price=e_cost,
                                   quantity=int(e_qty), buy_date=e_date.strip())
                else:
                    _holder.delete([old_code])
                    _holder.add(new_code, e_name.strip() or new_code, e_market,
                                e_cost, int(e_qty), e_date.strip())
                st.success("已更新")
                st.rerun()

# =========================== 删除持仓 =========================== #
with tab_delete:
    st.subheader("🗑️ 删除持仓")
    positions = _holder.all()
    if not positions:
        st.info("暂无持仓可删除")
    else:
        labels = [f"{i + 1}. {_label(p)}" for i, p in enumerate(positions)]
        to_delete = st.multiselect("勾选要删除的持仓", range(len(positions)),
                                   format_func=lambda i: labels[i])
        if st.button("🗑️ 删除选中", use_container_width=True):
            if not to_delete:
                st.warning("请先勾选要删除的持仓")
            else:
                codes = [positions[i]["code"] for i in to_delete]
                _holder.delete(codes)
                st.success(f"已删除 {len(to_delete)} 条")
                st.rerun()
