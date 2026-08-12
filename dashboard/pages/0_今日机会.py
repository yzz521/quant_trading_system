"""V2 今日机会 —— 围绕「每日投资决策」重设计的首页。

功能：
  * 市场状态卡片（Regime / 宽度 / 风险）
  * 输入股票 → 完整交易计划（评分/入场/止损/目标/RR/仓位）
  * AI 解读（无 AI key 时规则化兜底）
  * 该股票的历史回测摘要（验证规则有效性）

Run::

    streamlit run quant_trading_system/dashboard/pages/0_今日机会.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import streamlit as st
from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
from quant_trading_system.stock_analysis import (
    StockDiagnoser,
    add_all_indicators,
    detect_market,
    fetch_kline,
)
from quant_trading_system.stock_analysis.ai import explain_plan
from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.market import (
    calc_market_breadth,
    fetch_market_context,
)
from quant_trading_system.stock_analysis.opportunity import OpportunityEngine
from quant_trading_system.utils import load_yaml

apply_theme()
require_login()
page_header("今日机会", "每日投资决策 · V2", "Opportunity")

ACCOUNT = 100_000  # 默认账户资金（元）

# --------------------------------------------------------------------------- #
# 市场状态
# --------------------------------------------------------------------------- #
st.subheader("📈 市场状态")
try:
    # 真实指数（上证指数）→ 市场状态/风险；全市场快照 → 宽度
    spot = None
    try:
        from quant_trading_system.stock_analysis.data_fetcher import fetch_spot_snapshot

        spot = fetch_spot_snapshot()
    except Exception:  # noqa: BLE001
        spot = None

    breadth = calc_market_breadth(spot) if spot is not None else None
    # 指数失败时降级中性，不阻塞页面
    mkt = fetch_market_context("sh000001")
    regime = mkt.get("regime")
    risk = mkt.get("risk")
except Exception as e:  # noqa: BLE001
    st.warning(f"市场状态获取失败（不影响个股分析）: {e}")
    breadth, regime, risk = None, None, None

if regime is not None:
    c1, c2, c3 = st.columns(3)
    emoji = {"BULL": "🐂", "NEUTRAL": "➖", "BEAR": "🐻", "HIGH_RISK": "⚠️"}
    c1.metric("市场状态", f"{emoji.get(regime.state.value, '')} {regime.state.value}", f"分 {regime.score:.0f}")
    if breadth is not None:
        c2.metric("市场宽度", f"涨 {breadth.advance} / 跌 {breadth.decline}", f"宽度分 {breadth.score:.0f}")
    c3.metric("市场风险", risk.level if risk else "LOW", f"分 {risk.score:.0f}")

st.divider()

# --------------------------------------------------------------------------- #
# 个股交易计划
# --------------------------------------------------------------------------- #
st.subheader("🎯 个股交易计划")
col1, col2, col3 = st.columns([1, 1, 1])
code = col1.text_input("股票代码", "600000", help="A股 6 位代码")
account = col2.number_input("账户资金（元）", value=ACCOUNT, step=10_000)
run = col3.button("生成交易计划", type="primary", use_container_width=True)

if run:
    with st.spinner(f"正在分析 {code} ..."):
        try:
            info = detect_market(code)
            raw = fetch_kline(info, days=250)
            if raw is None or raw.empty:
                st.error(f"无法获取 {code} 行情")
                st.stop()
            df = add_all_indicators(raw)

            diag = StockDiagnoser().diagnose(code)
            # 从诊断的估值/资金流字段提取 Stock Score 需要的 extra 数据
            val = diag.valuation or {}
            ff = diag.fund_flow or {}
            extra = {
                "pe": val.get("pe_ttm") or val.get("pe"),
                "total_cap_yi": val.get("market_cap") or val.get("total_cap_yi"),
                "turnover": ff.get("turnover"),
                "main_net": ff.get("main_net"),
            }
            extra = {k: v for k, v in extra.items() if v is not None}

            engine = OpportunityEngine(
                account_equity=account,
                regime_score=regime.score if regime else None,
                market_factor=regime.factor if regime else 1.0,
            )
            res = engine.analyze(info.code, info.code, df, extra=extra)
            if res.plan is None:
                st.error("数据不足，无法生成交易计划")
                st.stop()
        except Exception as e:  # noqa: BLE001
            st.error(f"分析失败: {e}")
            st.stop()

    p = res.plan
    emoji = p.decision.emoji
    st.markdown(f"### {emoji} {p.name} ({p.code})　**{p.decision.value}**")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("个股评分", f"{p.stock_score}/100")
    m2.metric("机会评分", f"{p.opportunity_score}/100")
    m3.metric("置信度", f"{p.confidence:.0%}")
    m4.metric("建议仓位", f"{p.position_percent:.1f}%" if p.position_percent is not None else "—")

    c1, c2, c3 = st.columns(3)
    c1.metric("现价", f"{p.current_price:.2f}")
    c2.metric("入场区间", f"{p.entry_low:.2f} ~ {p.entry_high:.2f}")
    c3.metric("止损", f"{p.stop_loss:.2f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("目标 T1", f"{p.target_1:.2f}" if p.target_1 else "—")
    c2.metric("目标 T2", f"{p.target_2:.2f}" if p.target_2 else "—")
    c3.metric("目标 T3", f"{p.target_3:.2f}" if p.target_3 else "—")
    c4.metric("风险收益比", f"1:{p.risk_reward_1}" if p.risk_reward_1 else "—")

    if p.reasons:
        st.markdown("**理由**")
        for r in p.reasons:
            st.markdown(f"- {r}")
    if p.risks:
        st.markdown("**风险**")
        for r in p.risks:
            st.markdown(f"- {r}")
    if p.invalidate_condition:
        st.warning(f"失效条件: {p.invalidate_condition}")

    # ---- AI 解读 ----
    st.divider()
    st.markdown("#### 🤖 AI 解读")
    with st.spinner("AI 解读中..."):
        try:
            cfg = load_yaml(str(Path(__file__).resolve().parents[2] / "config" / "notify.yaml"))
        except Exception:  # noqa: BLE001
            cfg = None
        ai_text = explain_plan(p, notify_cfg=cfg)
    st.markdown(ai_text)

    # ---- 历史回测 ----
    st.divider()
    st.markdown("#### 📊 历史规则回测")
    with st.spinner("回测中..."):
        bt = TradingPlanBacktest(engine=engine, stride=5)
        bt_res = bt.run(df, info.code, info.code)
    if bt_res.metrics and bt_res.metrics.sample_size > 0:
        m = bt_res.metrics
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("样本计划数", m.sample_size)
        r2.metric("入场区命中率", f"{m.entry_zone_hit_rate:.0%}")
        r3.metric("止损触发率", f"{m.stop_loss_trigger_rate:.0%}")
        r4.metric("T1 命中率", f"{m.target_1_hit_rate:.0%}")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("胜率", f"{m.win_rate:.0%}")
        r2.metric("平均收益", f"{m.avg_return:.2f}%")
        r3.metric("平均持有", f"{m.avg_holding_period:.1f} 日")
        r4.metric("最大回撤", f"{m.max_drawdown:.2f}%")
        st.caption("回测为历史规则有效性验证，不构成对未来表现的保证。")
    else:
        st.info("样本不足，未生成回测。")

# --------------------------------------------------------------------------- #
# 批量机会扫描
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("📋 批量机会扫描")
bcol1, bcol2, bcol3 = st.columns([2, 1, 1])
batch_input = bcol1.text_input(
    "候选股票代码（逗号分隔）",
    "600000, 000001, 600519, 601318",
    help="逐个跑机会引擎，输出按机会分排序的交易计划（过滤 AVOID）",
)
batch_account = bcol2.number_input("批量账户资金（元）", value=ACCOUNT, step=10_000)
batch_run = bcol3.button("批量扫描", type="secondary", use_container_width=True)

if batch_run:
    codes = [c.strip() for c in batch_input.replace("，", ",").split(",") if c.strip()]
    if not codes:
        st.warning("请输入至少一个股票代码")
    else:
        with st.spinner(f"批量分析 {len(codes)} 只 ..."):
            from quant_trading_system.stock_analysis.opportunity import OpportunityBatchScanner

            engine = OpportunityEngine(
                account_equity=batch_account,
                regime_score=regime.score if regime else None,
                market_factor=regime.factor if regime else 1.0,
            )
            scanner = OpportunityBatchScanner(engine=engine, workers=5)
            res = scanner.scan(codes, market="CN")
        if res.plans:
            st.success(f"生成 {len(res.plans)} 个有效计划（AVOID 已过滤）")
            rows = []
            for p in res.plans:
                rows.append({
                    "决策": f"{p.get('decision_emoji','')} {p.get('decision','')}",
                    "代码": p.get("code"),
                    "名称": p.get("name"),
                    "个股分": p.get("stock_score"),
                    "机会分": p.get("opportunity_score"),
                    "现价": p.get("current_price"),
                    "入场区间": f"{p.get('entry_low')}~{p.get('entry_high')}",
                    "止损": p.get("stop_loss"),
                    "目标T1/T2": f"{p.get('target_1')}/{p.get('target_2')}",
                    "风险收益": f"1:{p.get('risk_reward_1')}",
                    "仓位%": p.get("position_percent"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.warning("本轮无有效机会（可能全部 AVOID 或数据不足）")
        if res.failed:
            with st.expander(f"⚠️ {len(res.failed)} 只分析失败"):
                for f in res.failed:
                    st.write(f"- {f.get('name')}({f.get('code')}): {f.get('error')}")
        st.caption(f"耗时 {res.elapsed:.1f}s")
