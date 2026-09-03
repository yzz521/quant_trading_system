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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import streamlit as st
from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
from quant_trading_system.dashboard.paths import holdings_config, notify_config
from quant_trading_system.stock_analysis.data_fetcher import detect_market, fetch_name
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.holdings_action import analyze_holding_actions
from quant_trading_system.stock_analysis.holdings_quant import (
    analyze_holdings_quant,
    cached_items,
    save_market_cache,
    session_date,
)
from quant_trading_system.stock_analysis.sell_zone import analyze_positions
from quant_trading_system.stock_analysis.trade_monitor import TradeMonitor

apply_theme()
require_login()
page_header("持仓指挥台", "资金约束 · 本地账本 · 卖出区间与动作建议", "Holdings")

MARKETS = ["CN", "US", "HK"]

# 兼容旧路径：Holdings 内部会自动切换到同目录的 holdings.db
HOLDINGS_CFG = holdings_config()
NOTIFY_CFG = notify_config()


def get_holdings() -> Holdings:
    return Holdings(HOLDINGS_CFG)


def _label(p: dict) -> str:
    return f"{p['code']} {p.get('name', '')}".strip()


def _pfmt(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "-"


# --------------------------------------------------------------------------- #

_holder = get_holdings()
st.caption(f"数据存储：`{_holder.db_path}`（本地 SQLite，不入库；调度器推送时读取同一数据库）")

tab_overview, tab_sell, tab_paste, tab_add, tab_edit, tab_delete, tab_sellzone = st.tabs(
    ["📊 持仓总览", "💸 记录卖出", "📋 粘贴成交", "➕ 新增持仓", "✏️ 编辑持仓",
     "🗑️ 删除持仓", "🎯 卖出区间"]
)

# =========================== 持仓总览 =========================== #
with tab_overview:

    # ---- 资金账户 ----
    st.markdown("##### 💰 资金账户（计划投入）")
    acc = _holder.get_account()
    snap = _holder.capital_snapshot()
    ca1, ca2, ca3, ca4 = st.columns([1.2, 1, 1, 1])
    with ca1:
        new_cap = st.number_input(
            "总资金（元）", min_value=0.0, value=float(acc.get("total_capital") or 0),
            step=1000.0, key="acct_total_cap",
            help="计划投入股市的预算。填 0 表示不启用可买性过滤。",
        )
    with ca2:
        new_pct = st.number_input(
            "单票上限 %", min_value=5.0, max_value=100.0,
            value=float(acc.get("max_position_pct") or 0.30) * 100,
            step=5.0, key="acct_max_pct",
        )
    if st.button("保存资金设置", key="save_acct"):
        from quant_trading_system.dashboard.capital import save_planned_capital

        save_planned_capital(float(new_cap))
        _holder.set_account(max_position_pct=float(new_pct) / 100.0)
        st.success("已保存")
        st.rerun()
    snap = _holder.capital_snapshot()
    if snap:
        ca3.metric("持仓占用(成本)", f"{snap['invested_cost']:,.0f}")
        ca4.metric("可用资金", f"{snap['available_cash']:,.0f}",
                   f"使用率 {snap['utilization_pct']}%")
        if snap["available_cash"] <= 0:
            st.caption("可用为 0：新开仓推荐将标为资金不足；以持仓卖出/减仓腾出额度为主。")
    else:
        st.caption("未设置总资金时，邮件推荐不做「买不买得起」标注。")
    st.divider()

    st.info(
        "持仓以本系统 **config/holdings.db** 为准。"
        "券商里卖出后，请用「💸 记录卖出」或「📋 粘贴成交」同步；"
        "macOS 可选用成交监听。系统**不会**自动读取券商账户。"
    )

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

        st.markdown("**📐 今日持仓量化**")
        st.caption("按已持有解读（卖出/减仓/持有/可加仓）。调度器每个交易日自动跑一次并写入邮件；此处可立即刷新。不跑历史回测。")
        today = session_date()
        if "holdings_quant" not in st.session_state:
            codes = {str(p.get("code")) for p in positions}
            merged = []
            for m in MARKETS:
                hit = cached_items(m, today)
                if hit:
                    merged.extend([x for x in hit if str(x.get("code")) in codes])
            if merged:
                st.session_state["holdings_quant"] = merged
        if st.button("刷新持仓量化", key="run_holdings_quant"):
            with st.spinner("正在分析持仓（K线 + 技术面 + 信息面）..."):
                try:
                    rows_q = positions
                    try:
                        rows_q, _ = _holder.compute_pnl()
                    except Exception:  # noqa: BLE001
                        pass
                    zones = {}
                    try:
                        for a in analyze_holding_actions(rows_q):
                            if a.get("code") and not a.get("error"):
                                zones[str(a["code"])] = a
                    except Exception:  # noqa: BLE001
                        zones = {}
                    items = analyze_holdings_quant(rows_q, fetch_news=True, zones=zones)
                    st.session_state["holdings_quant"] = items
                    by_m: dict[str, list] = {}
                    for a in items:
                        by_m.setdefault(str(a.get("market") or "CN"), []).append(a)
                    for m, rows in by_m.items():
                        save_market_cache(m, today, rows)
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
        items = st.session_state.get("holdings_quant")
        if items:
            qrows = []
            for a in items:
                qrows.append({
                    "动作": f"{a.get('action_emoji','')} {a.get('action_label')}",
                    "代码": a.get("code"),
                    "名称": a.get("name"),
                    "现价": a.get("current_price"),
                    "盈亏%": a.get("pnl_pct"),
                    "个股/机会": f"{a.get('stock_score')}/{a.get('opportunity_score')}",
                    "技术/信息": f"{a.get('tech_grade')} / {a.get('info_grade')}",
                    "止损": a.get("stop_loss"),
                    "说明": a.get("note") or a.get("error") or "",
                })
            st.dataframe(pd.DataFrame(qrows), use_container_width=True, hide_index=True)

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


# =========================== 记录卖出 =========================== #
with tab_sell:
    st.subheader("💸 记录卖出（同步本地持仓）")
    st.caption(
        "在券商 App 完成卖出后，在这里登记数量，系统才会更新持仓。"
        "全部卖出则自动从列表移除；部分卖出只减数量、成本价不变。"
    )
    positions = _holder.all()
    if not positions:
        st.warning("当前无持仓可卖")
    else:
        labels = [f"{p['code']} {p.get('name','')}（持有 {int(float(p['quantity']))} 股）"
                  for p in positions]
        sel = st.selectbox("选择标的", range(len(positions)),
                           format_func=lambda i: labels[i], key="sell_sel")
        p = positions[sel]
        held = int(float(p["quantity"]))
        c1, c2 = st.columns(2)
        sell_qty = c1.number_input(
            "卖出数量（股）", min_value=1, max_value=held,
            value=min(100, held) if held >= 100 else held, step=100,
            key="sell_qty",
        )
        c2.metric("卖后剩余", f"{held - int(sell_qty)} 股")
        clear = st.checkbox("全部卖出（清仓）", value=False, key="sell_clear")
        if clear:
            sell_qty = held
            st.warning(f"将清仓 {p['code']} 全部 {held} 股")
        if st.button("确认登记卖出", type="primary", use_container_width=True):
            try:
                msg = _holder.apply_sell(p["code"], int(sell_qty))
                st.success(msg)
                st.rerun()
            except Exception as e:
                st.error(str(e))

# =========================== 卖出区间分析 =========================== #
with tab_sellzone:
    st.subheader("🎯 卖出区间分析")
    st.caption("对每只持仓：基于最近约一年日 K 线，综合成本价止盈位（+10%/+20%/+30%）与"
               "均线 / 布林带 / 前高压力位，给出建议卖出区间与止损参考；若持仓有变动请重新分析。")
    if st.button("🔍 开始分析", type="primary", use_container_width=True):
        with st.spinner("获取行情并计算中（每只约 1~3 秒）..."):
            st.session_state["sellzone_result"] = analyze_positions(_holder.all())

    results = st.session_state.get("sellzone_result")
    if results is None:
        st.info("点击上方按钮，对当前全部持仓进行卖出区间分析。")
    else:
        ok = [r for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]

        if ok:
            rows = []
            for r in ok:
                row = {
                    "代码": r["code"], "名称": r["name"],
                    "现价": r["current_price"], "成本": r["cost_price"],
                    "盈亏%": f"{r['pnl_pct']:+.1f}",
                    "建议卖出区间": f"{r['zone_lo']} ~ {r['zone_hi']}",
                    "区间依据": f"{r['zone_lo_label']} → {r['zone_hi_label']}",
                    "止损参考": _pfmt(r["stop_loss"]),
                }
                if r.get("regime") == "deep_loss" and r.get("stage1_lo") is not None:
                    row["第一目标(分批)"] = f"{r['stage1_lo']} ~ {r['stage1_hi']}"
                    row["最终回本"] = r.get("stage2_price")
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"共分析 {len(ok)} 只持仓")

            for r in ok:
                with st.expander(f"📌 {r['code']} {r['name']} — {r['advice']}"):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("现价", f"{r['current_price']:.4f}")
                    c2.metric("成本", f"{r['cost_price']:.4f}")
                    c3.metric("盈亏", f"{r['pnl_pct']:+.2f}%")
                    c4.metric("止损参考", _pfmt(r["stop_loss"]))
                    if r.get("regime") == "deep_loss" and r.get("stage1_lo") is not None:
                        st.info(
                            f"**分批路径：** 第一目标 {r['stage1_lo']} ~ {r['stage1_hi']} "
                            f"（{r.get('stage1_lo_label','')} → {r.get('stage1_hi_label','')}）；"
                            f"最终回本 **{r.get('stage2_price')}**"
                        )
                    if r["targets"]:
                        def _tdf(items, title):
                            if not items:
                                return
                            st.markdown(title)
                            st.dataframe(pd.DataFrame([{
                                "目标价": t["price"],
                                "依据": " / ".join(t["labels"]),
                                "距现价": f"{t['pct']:+.1f}%",
                            } for t in items]), use_container_width=True, hide_index=True)

                        if r.get("regime") == "deep_loss":
                            near = [t for t in r["targets"] if t.get("tier") in ("near", None) and t["price"] < r["cost_price"]]
                            exit_ = [t for t in r["targets"] if t.get("tier") == "exit" or abs(t["price"] - r["cost_price"]) / max(r["cost_price"], 1e-9) <= 0.005]
                            far = [t for t in r["targets"] if t.get("tier") == "far" or t["price"] > r["cost_price"] * 1.005]
                            # de-dup: exit rows not in near
                            near = [t for t in near if t not in exit_]
                            _tdf(near, "**近端目标（反弹减仓，优先关注）：**")
                            _tdf(exit_, "**回本目标：**")
                            # Streamlit 禁止 expander 嵌套；用 checkbox 控制远期止盈表
                            show_far = st.checkbox(
                                "显示解套后的止盈阶梯（成本+10/20/30%，距现价较远）",
                                value=False,
                                key=f"far_targets_{r['code']}",
                            )
                            if show_far:
                                if far:
                                    st.dataframe(pd.DataFrame([{
                                        "目标价": t["price"],
                                        "依据": " / ".join(t["labels"]),
                                        "距现价": f"{t['pct']:+.1f}%",
                                    } for t in far]), use_container_width=True, hide_index=True)
                                else:
                                    st.caption("无成本上方止盈档。")
                        else:
                            st.markdown("**当前价上方目标位：**")
                            st.dataframe(pd.DataFrame([{
                                "目标价": t["price"],
                                "依据": " / ".join(t["labels"]),
                                "距现价": f"{t['pct']:+.1f}%",
                            } for t in r["targets"]]), use_container_width=True, hide_index=True)
                    else:
                        st.caption("当前价已高于所有参考压力位（创出新高），目标按波动率(ATR)推算。")
                    st.markdown(
                        f"**均线：** MA5={_pfmt(r['ma5'])} MA10={_pfmt(r['ma10'])} "
                        f"MA20={_pfmt(r['ma20'])} MA60={_pfmt(r['ma60'])}  \n"
                        f"**布林带：** {_pfmt(r['boll_lower'])} / {_pfmt(r['boll_mid'])} / "
                        f"{_pfmt(r['boll_upper'])}（下/中/上）  \n"
                        f"**近期高点：** 20日={_pfmt(r['high20'])} 60日={_pfmt(r['high60'])} "
                        f"120日={_pfmt(r['high120'])}  \n"
                        f"**ATR(14)：** {_pfmt(r['atr'])}（每日平均波动幅度）"
                    )

        if bad:
            st.warning(f"以下 {len(bad)} 只持仓分析失败（不影响其它持仓）：")
            for r in bad:
                st.write(f"• {r.get('code', '-')} {r.get('name', '')} — {r['error']}")

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
