"""V2 今日机会 —— 三市场合并：进入即看 A股/港股/美股 全部推荐。

布局：
  1. 📈 市场状态（A股上证指数 + 宽度 + 风险）
  2. 🎯 今日推荐（三市场同时扫描，进度条；跨市场总览 + 三市场 tab 明细）
  3. 🔍 单只详情（跨市场下拉选一只，AI 解读 + 历史回测）
  4. 🛠 自定义扫描（手动输入任意代码备用）

Run::

    streamlit run quant_trading_system/dashboard/pages/0_opportunity.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import streamlit as st
from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.capital import planned_capital, save_planned_capital
from quant_trading_system.dashboard.disclaimer import render_disclaimer
from quant_trading_system.dashboard.paths import notify_config
from quant_trading_system.dashboard.realtime_status import render_badge
from quant_trading_system.dashboard.ui_theme import apply_theme, page_header, scan_banner_html
from quant_trading_system.stock_analysis import (
    add_all_indicators,
    detect_market,
    fetch_kline,
)
from quant_trading_system.stock_analysis.ai import explain_plan_with_review
from quant_trading_system.stock_analysis.app_config import (
    MARKET_LABELS_UI,
    enabled_markets,
    load_app_config,
)
from quant_trading_system.stock_analysis.backtest import TradingPlanBacktest
from quant_trading_system.stock_analysis.data_fetcher import (
    fetch_fund_flow,
    fetch_valuation,
)
from quant_trading_system.stock_analysis.market import (
    calc_market_breadth,
    fetch_market_context,
)
from quant_trading_system.stock_analysis.opportunity import (
    OpportunityBatchScanner,
    OpportunityEngine,
)
from quant_trading_system.utils import load_yaml

apply_theme()
require_login()
page_header("今日机会", "每日投资决策 · V2", "Opportunity")
# 快轨状态条：一眼看出实时盯盘在不在跑（详情在「配置 → 实时盯盘」）
render_badge()

# --------------------------------------------------------------------------- #
# 顶部扫描状态条
# --------------------------------------------------------------------------- #
# 放在所有耗时操作之前：本页首个「获取全市场快照」要跑数秒，若等它跑完才有内容，
# 页面在这段时间是纯空白的，用户无法区分「没加载」和「正在分析」。
# 这里先占好位置并立刻渲染一条带旋转动画的状态，之后每个阶段原地更新文案。
scan_slot = st.empty()


def _set_scan(text: str, state: str = "run") -> None:
    """更新顶部状态条（run=运行中带动画 / done=完成 / err=失败）。"""
    scan_slot.markdown(scan_banner_html(text, state), unsafe_allow_html=True)


_set_scan("正在初始化…")

_app_cfg = load_app_config(notify_config())
_opp_cfg = _app_cfg.get("opportunity") or {}
ACCOUNT = planned_capital()
DEFAULT_TOP_N = int(_opp_cfg.get("max_stocks") or 30)
SCAN_WORKERS = int(_opp_cfg.get("workers") or 5)
MARKETS = enabled_markets(_app_cfg)
MARKET_LABELS = {m: MARKET_LABELS_UI.get(m, m) for m in MARKETS}


@st.cache_data(ttl=600, show_spinner=False)
def _cached_spot():
    """A股全市场快照（缓存 10 分钟，顶部市场状态与 CN 初筛共享）。"""
    from quant_trading_system.stock_analysis.data_fetcher import fetch_spot_snapshot
    return fetch_spot_snapshot()


@st.cache_data(ttl=300, show_spinner=False)
def _active_weights() -> tuple:
    """当前生效的组合分权重（按因子验证结论自动降权，缓存 5 分钟）。

    与批量扫描器共用同一套判定（``scoring.factor_weights``），保证**看板显示的
    组合分与后台排序口径一致** —— 否则「按组合分降序」的表会出现分数不降序的怪象。
    """
    from quant_trading_system.stock_analysis.scoring.composite import COMPOSITE_WEIGHTS
    from quant_trading_system.stock_analysis.scoring.factor_weights import active_weights

    try:
        w, meta = active_weights()
        return dict(w), dict(meta)
    except Exception as e:  # noqa: BLE001
        return dict(COMPOSITE_WEIGHTS), {
            "applied": False, "reason": f"读取因子验证报告失败：{e}", "notes": [],
        }


_W, _W_META = _active_weights()


# --------------------------------------------------------------------------- #
# 市场状态（自动加载）
# --------------------------------------------------------------------------- #
st.subheader("📈 市场状态")
try:
    _set_scan("正在获取市场状态（全市场快照 + 上证指数）…")
    spot = None
    try:
        spot = _cached_spot()
    except Exception:  # noqa: BLE001
        spot = None

    breadth = calc_market_breadth(spot) if spot is not None else None
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
st.caption(
    "市场状态为 A 股（上证指数）视角；港股/美股机会按中性市场环境评估。"
    "监测市场请到左侧 **settings** 页勾选。"
)

st.divider()


# --------------------------------------------------------------------------- #
# 今日推荐（三市场同时扫描）
# --------------------------------------------------------------------------- #
st.subheader("🎯 今日推荐")
if ACCOUNT <= 0:
    _set_scan("尚未设置预计投入金额，已暂停扫描", state="err")
    st.warning("尚未设置预计投入金额。设置后才会扫描今日机会（用于计算建议仓位）。")
    setup_cap = st.number_input(
        "预计投入金额（元）",
        min_value=0.0,
        value=100_000.0,
        step=10_000.0,
        key="setup_capital",
        help="写入持仓「资金账户」，下次进入本页会自动读取。",
    )
    if st.button("保存并开始扫描", type="primary", key="save_setup_capital"):
        if float(setup_cap) <= 0:
            st.error("请填写大于 0 的金额")
        else:
            save_planned_capital(float(setup_cap))
            st.success("已保存，开始扫描…")
            st.rerun()
    st.caption("也可到左侧 **holdings** 页「资金账户」中设置。")
    st.stop()

st.caption(f"仓位按预计投入 **{ACCOUNT:,.0f} 元** 计算（持仓配置）。修改请到 holdings 页。")
top_n = st.number_input(
    "每市场候选数",
    value=max(5, min(80, DEFAULT_TOP_N)),
    min_value=5,
    max_value=80,
    step=5,
    key="rec_topn",
)


@st.cache_data(ttl=600, show_spinner=False)
def _scan_market(market: str, top_n: int, account_eq: float,
                 regime_score: float | None, market_factor: float, workers: int):
    """单市场：全市场初筛 → 批量机会引擎（缓存 10 分钟）。返回 (cands, res)。

    * 行业映射在初筛**之前**取：初筛要用它做行业分层取样，避免候选池被单一行业垄断。
    * 市场状态只在 CN 生效；港股/美股用中性环境（此前统一套用上证状态，属口径错误）。
    """
    from quant_trading_system.stock_analysis.screener import screen_candidates
    from quant_trading_system.stock_analysis.sector import fetch_sector_rank, get_stock_sectors

    try:
        sector_rank, sector_map = [], {}
        if market == "CN":
            try:
                sector_rank = fetch_sector_rank("CN")
                sector_map = get_stock_sectors()
            except Exception:  # noqa: BLE001
                sector_rank, sector_map = [], {}
        cn_regime = regime_score if market == "CN" else None
        cn_factor = market_factor if market == "CN" else 1.0

        cands = screen_candidates(
            market, top_n=top_n, industry_map=sector_map or None
        )
        if not cands:
            return [], None
        eng = OpportunityEngine(
            account_equity=account_eq,
            regime_score=cn_regime,
            market_factor=cn_factor,
            sector_map=sector_map,
            sector_rank=sector_rank,
            fetch_news=True,
        )
        # HK 用 akshare（非线程安全）→ 并发降到 2
        n_workers = 2 if market == "HK" else max(1, int(workers))
        scanner = OpportunityBatchScanner(engine=eng, workers=n_workers)
        return cands, scanner.scan(cands, market=market)
    except Exception:  # noqa: BLE001
        return [], None


scan_res: dict = {m: ([], None) for m in MARKETS}  # 预填，避免中途异常导致 KeyError
n_mkt = max(len(MARKETS), 1)
# 状态反馈统一走页面顶部的扫描状态条（sticky 吸顶 + 旋转动画），这里只保留
# 中间列的一根细进度条做次级提示；扫描完成后清空，不留长期占版面的块。
prog_slot = st.columns([1, 3, 1])[1].empty()
try:
    prog_slot.progress(0.0, text="准备中…")
    for i, m in enumerate(MARKETS):
        _set_scan(
            f"正在扫描 <b>{i + 1}/{n_mkt}</b>：{MARKET_LABELS[m]}（初筛 + 机会引擎，约 10-20 秒）…"
        )
        scan_res[m] = _scan_market(
            m, int(top_n), ACCOUNT,
            regime.score if regime else None,
            regime.factor if regime else 1.0,
            SCAN_WORKERS,
        )
        prog_slot.progress((i + 1) / n_mkt, text=f"已完成 {i + 1}/{n_mkt}")
    prog_slot.empty()
    summary_bits = []
    n_failed = 0
    for m in MARKETS:
        cands, res = scan_res[m]
        n_c = len(cands)
        n_p = len(res.plans) if res is not None else 0
        el = f"{res.elapsed:.0f}s" if res is not None else "—"
        if res is not None:
            n_failed += len(res.failed)
        summary_bits.append(f"{MARKET_LABELS[m]} {n_c}只→{n_p}计划({el})")
    _tail = f"，{n_failed} 只失败" if n_failed else ""
    _set_scan(
        f"扫描完成 · {datetime.now():%H:%M} · {' | '.join(summary_bits)}{_tail}",
        state="done",
    )
except Exception as e:  # noqa: BLE001
    prog_slot.empty()
    _set_scan(f"扫描失败：{e}", state="err")


def _to_rows(plans: list) -> list:
    from quant_trading_system.stock_analysis.scoring.composite import score_plan

    out = []
    for p in plans:
        meta = p.get("meta") or {}
        gate = meta.get("quality_gate") or {}
        cov = (gate.get("coverage") or {}).get("ratio")
        out.append({
            "代码·名称": f"{p.get('code')} {p.get('name')}",
            "板块": meta.get("sector") or "—",
            "组合分": round(score_plan(p, _W), 1),
            "个股分": p.get("stock_score"),
            "机会分": p.get("opportunity_score"),
            "质量闸门": {"BUY": "✅ 通过", "WATCH": "🟡 降级", "REJECT": "⛔ 否决"}.get(
                str(gate.get("tier") or ""), "—"
            ),
            "数据完整度": f"{cov:.0%}" if isinstance(cov, (int, float)) else "—",
            "现价": p.get("current_price"),
            "入场区间": f"{p.get('entry_low')}~{p.get('entry_high')}",
            "止损": p.get("stop_loss"),
            "目标T1/T2": f"{p.get('target_1')}/{p.get('target_2')}",
            "RR": f"1:{p.get('risk_reward_1')}",
            "仓位%": p.get("position_percent"),
        })
    return out


def _render_gate_note(market: str) -> None:
    """显示本轮闸门口径说明（例如未取到财务数据导致的放宽）。"""
    _, res = scan_res.get(market, ([], None))
    if res is not None and getattr(res, "gate_note", ""):
        st.warning(res.gate_note)


def _show_market_tab(market: str):
    """单个市场的买入/关注列表（不嵌套 tabs）。"""
    cands, res = scan_res[market]
    if not cands:
        st.warning("全市场快照不可用（网络/数据源问题）")
        return
    if res is None or not res.plans:
        st.warning("该市场无有效机会（可能全部 AVOID 或数据不足）")
        return
    buy = [p for p in res.plans if p.get("decision") in ("BUY_NOW", "BUY_ON_PULLBACK")]
    watch = [p for p in res.plans if p.get("decision") == "WATCH"]
    _render_gate_note(market)
    st.markdown("**🟢 买入列表**")
    if buy:
        st.dataframe(pd.DataFrame(_to_rows(buy)), use_container_width=True, hide_index=True)
    else:
        st.info("当前无符合买入条件的标的（BUY_NOW / BUY_ON_PULLBACK）")
    st.markdown("**🟡 关注列表**")
    if watch:
        st.dataframe(pd.DataFrame(_to_rows(watch)), use_container_width=True, hide_index=True)
        st.caption(
            "关注列表含**未通过质量闸门而被降级**的标的 —— 展开单只详情可看到具体"
            "不通过原因（分数不足 / 数据缺失 / 风险偏高）。"
        )
    else:
        st.info("当前无关注标的（WATCH）")
    if res.failed:
        with st.expander(f"⚠️ {len(res.failed)} 只分析失败（数据源问题，不影响推荐）"):
            for f in res.failed:
                st.write(f"- {f.get('name')}({f.get('code')}): {f.get('error')}")


# 跨市场总览（合并买入计划，含市场列，机会分降序）
all_buy = []
for m in MARKETS:
    cands, res = scan_res[m]
    if res is not None:
        for p in res.plans:
            if p.get("decision") in ("BUY_NOW", "BUY_ON_PULLBACK"):
                all_buy.append({"market": m, **p})
if all_buy:
    from quant_trading_system.stock_analysis.scoring.composite import score_plan

    st.markdown("**📊 跨市场总览（买入候选）**")
    rows = []
    for p in sorted(all_buy, key=lambda x: score_plan(x, _W), reverse=True):
        meta = p.get("meta") or {}
        gate = meta.get("quality_gate") or {}
        cov = (gate.get("coverage") or {}).get("ratio")
        rows.append({
            "市场": MARKET_LABELS.get(p["market"], p["market"]),
            "代码·名称": f"{p.get('code')} {p.get('name')}",
            "板块": meta.get("sector") or "—",
            "组合分": round(score_plan(p, _W), 1),
            "个股分": p.get("stock_score"),
            "机会分": p.get("opportunity_score"),
            "数据完整度": f"{cov:.0%}" if isinstance(cov, (int, float)) else "—",
            "现价": p.get("current_price"),
            "入场区间": f"{p.get('entry_low')}~{p.get('entry_high')}",
            "止损": p.get("stop_loss"),
            "RR": f"1:{p.get('risk_reward_1')}",
            "仓位%": p.get("position_percent"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    _w_txt = " + ".join(
        f"{ {'stock': '质量分', 'opportunity': '机会分', 'rr': '赔率'}.get(k, k) } {v:.0%}"
        for k, v in _W.items()
    )
    if _W_META.get("applied"):
        st.caption(
            f"组合分 = {_w_txt}（赔率按 RR 映射，4R 封顶）。"
            f"⚠️ 权重已**按因子验证结论自动降权**（非默认 45/35/20）："
            f"{_W_META.get('reason')} 详见「因子验证」页。"
        )
    else:
        st.caption(
            f"组合分 = {_w_txt}（赔率按 RR 映射，4R 封顶）。"
            f"{_W_META.get('reason') or ''}"
            "排序已从「只按机会分」改为组合分，避免技术形态压过公司质量。"
        )

# 三市场明细 tabs
mkt_tabs = st.tabs([f"{MARKET_LABELS[m]}" for m in MARKETS])
for tab, m in zip(mkt_tabs, MARKETS):
    with tab:
        _show_market_tab(m)


# --------------------------------------------------------------------------- #
# 单只详情（跨市场下拉，sel_code 所有分支安全定义）
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("🔍 单只详情")

has_rec = any(cands and res is not None and bool(res.plans) for cands, res in scan_res.values())
sel_code, sel_name = "", ""  # 兜底默认值，杜绝 NameError

all_plans = [
    {"market": m, **p}
    for m in MARKETS
    for cands, res in [scan_res[m]]
    if res is not None
    for p in res.plans
]
if has_rec and all_plans:
    labels = {
        f"{MARKET_LABELS[p['market']]} {p['code']} {p['name']}": (p["code"], p["name"])
        for p in all_plans
    }
    sel = st.selectbox("🔍 选择查看详情（跨市场）", list(labels.keys()), index=0, key="rec_select")
    sel_code, sel_name = labels[sel]


@st.cache_data(ttl=900, show_spinner=False)
def _analyze_one(code: str, name: str, account_eq: float, regime_score: float | None, market_factor: float):
    """单股深度分析（拉 K 线 + 估值/资金流/成长 + 板块 + 机会引擎 + 回测），缓存 15 分钟。"""
    from quant_trading_system.stock_analysis.data_fetcher import fetch_growth_factors
    from quant_trading_system.stock_analysis.sector import (
        fetch_sector_rank,
        get_stock_sectors,
        sector_factor,
    )

    info = detect_market(code)
    # 市场状态只对 A 股有意义：港股/美股强制中性。放在这里而不是调用方，
    # 是为了保证「列表扫描」与「单票详情」不可能再出现两个口径 —— 原先列表
    # 已中性化而详情仍用上证指数，同一只票两处结论不一致。
    if info.market != "CN":
        regime_score, market_factor = None, 1.0
    raw = fetch_kline(info, days=250)
    if raw is None or raw.empty:
        return None
    df = add_all_indicators(raw)

    val = fetch_valuation(info)
    ff = fetch_fund_flow(info)
    extra: dict = {}

    if val is not None and not val.empty:
        row = val.iloc[-1]
        pe = None
        for col in ("pe_ttm", "total_pe", "pe"):
            if col in row.index:
                pe = row[col]
                break
        mv = None
        for col in ("total_mv", "market_cap"):
            if col in row.index:
                mv = row[col]
                break
        if pe is not None and not pd.isna(pe):
            extra["pe"] = float(pe)
        if mv is not None and not pd.isna(mv):
            extra["total_cap_yi"] = float(mv) / 1e8  # akshare 单位为元

    if ff is not None and not ff.empty:
        for col in ff.columns:
            if "主力" in col and "净额" in col:
                try:
                    net = float(ff[col].astype(float).tail(5).sum())
                    extra["main_net"] = net
                    extra["turnover"] = float(ff.iloc[-1].get("换手率", 0)) if "换手率" in ff.columns else None
                except (TypeError, ValueError):
                    pass
                break

    # Growth 成长因子（仅单票路径拉财务；失败忽略）
    try:
        g = fetch_growth_factors(info)
        if g:
            extra.update(g)
    except Exception:  # noqa: BLE001
        pass
    extra = {k: v for k, v in extra.items() if v is not None}

    # Sector Rotation：板块强度因子（A股）
    sector_map, sector_rank, stock_sector = {}, [], None
    sector_score = None
    try:
        if info.market == "CN":
            sector_map = get_stock_sectors()
            sector_rank = fetch_sector_rank("CN")
            stock_sector = sector_map.get(code)
            if stock_sector:
                sector_score = sector_factor(stock_sector, sector_rank)
    except Exception:  # noqa: BLE001
        sector_map, sector_rank = {}, []

    engine = OpportunityEngine(
        account_equity=account_eq,
        regime_score=regime_score,
        market_factor=market_factor,
        sector_map=sector_map,
        sector_rank=sector_rank,
        fetch_news=True,
    )
    plan_res = engine.analyze(code, name, df, extra=extra)
    if plan_res.plan is None:
        return None

    bt = TradingPlanBacktest(engine=engine, stride=5)
    bt_res = bt.run(df, code, name)

    return {"plan_res": plan_res, "df": df, "bt_res": bt_res, "stock_sector": stock_sector, "sector_score": sector_score}


# ---- 详情区（缓存命中时秒开；无推荐时占位） ----
if has_rec and sel_code:
    with st.spinner(f"⏳ 正在深度分析 {sel_code}（K线/估值/资金流/回测）..."):
        detail = _analyze_one(
            sel_code, sel_name, ACCOUNT,
            regime.score if regime else None,
            regime.factor if regime else 1.0,
        )
    if detail is None:
        st.error(f"{sel_code} 数据不足，无法生成详情")
    else:
        plan_res = detail["plan_res"]
        df = detail["df"]
        bt_res = detail["bt_res"]
        p = plan_res.plan

        st.markdown(f"### {p.decision.emoji} {p.name} ({p.code})　**{p.decision.value}**")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("个股评分", f"{p.stock_score}/100")
        m2.metric("机会评分", f"{p.opportunity_score}/100")
        m3.metric("置信度", f"{p.confidence:.0%}")
        m4.metric("建议仓位", f"{p.position_percent:.1f}%" if p.position_percent is not None else "—")

        # ---- 质量闸门与数据时点（决定「为什么不是买入」） ----
        _meta = p.meta or {}
        _gate = _meta.get("quality_gate") or {}
        _cov = _gate.get("coverage") or {}
        if _gate:
            tier = str(_gate.get("tier") or "")
            label = {"BUY": "✅ 通过", "WATCH": "🟡 降级为关注", "REJECT": "⛔ 硬否决"}.get(tier, tier)
            st.markdown(f"**质量闸门：{label}**")
            if _meta.get("gate_downgrade"):
                st.caption(f"决策调整：{_meta['gate_downgrade']}")
            if _gate.get("reasons"):
                st.warning("未通过原因：\n" + "\n".join(f"- {r}" for r in _gate["reasons"]))
            cov_ratio = _cov.get("ratio")
            st.caption(
                f"数据完整度 {cov_ratio:.0%}（{_cov.get('n_present', '—')}/{_cov.get('n_total', '—')} 字段）"
                + (f"；缺失：{'、'.join(_cov.get('missing') or [])}" if _cov.get("missing") else "")
            )
        _as_of = " · ".join(
            f"{k}={_meta[k]}" for k in ("price_as_of", "bar_as_of", "fundamental_as_of")
            if _meta.get(k)
        )
        if _as_of:
            st.caption(f"数据时点：{_as_of}")

        tech = (p.meta or {}).get("technical") or {}
        if tech.get("grade"):
            tags = " · ".join(tech.get("tags") or [])
            st.caption(f"技术面 **{tech['grade']}** 级（{tech.get('score', '—')}/100）" + (f"　{tags}" if tags else ""))
        info = (p.meta or {}).get("information") or {}
        if info.get("grade") and info.get("grade") != "中性":
            tags = " · ".join(info.get("tags") or [])
            st.caption(f"信息面 **{info['grade']}**（{info.get('score', '—')}/100）" + (f"　{tags}" if tags else ""))
        if info.get("severe"):
            st.warning("近两周公告/新闻命中重大风险关键词（立案/调查/处罚等），评分已降权，请先读原文再决策。")
        for h in (info.get("headlines") or [])[:3]:
            st.markdown(f"- {h}")
        pats = (p.meta or {}).get("patterns") or []
        if pats:
            st.caption("K线形态：" + "、".join(pats))

        c1, c2, c3 = st.columns(3)
        c1.metric("现价", f"{p.current_price:.2f}")
        c2.metric("入场区间", f"{p.entry_low:.2f} ~ {p.entry_high:.2f}")
        c3.metric("止损", f"{p.stop_loss:.2f}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("目标 T1", f"{p.target_1:.2f}" if p.target_1 else "—")
        c2.metric("目标 T2", f"{p.target_2:.2f}" if p.target_2 else "—")
        c3.metric("目标 T3", f"{p.target_3:.2f}" if p.target_3 else "—")
        c4.metric("风险收益比", f"1:{p.risk_reward_1}" if p.risk_reward_1 else "—")

        # 板块强度（Sector Rotation）
        stock_sector = detail.get("stock_sector")
        sector_score = detail.get("sector_score")
        if stock_sector:
            s1, s2 = st.columns(2)
            s1.metric("所属板块", stock_sector)
            s2.metric("板块强度", f"{sector_score:.0f}/100" if sector_score is not None else "—",
                      help="板块涨跌幅/成交额百分位合成（0-100），强势板块候选加分")

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

        st.markdown("#### 🤖 AI 解读")
        try:
            cfg = load_yaml(notify_config())
        except Exception:  # noqa: BLE001
            cfg = None
        with st.spinner("AI 解读中..."):
            ai_text, ai_review = explain_plan_with_review(p, notify_cfg=cfg)
        st.markdown(ai_text)
        if ai_review is not None and not ai_review.passed:
            # 校验不通过时展示层必须说清楚：这段文案已被丢弃，看到的是规则化解读
            st.warning("AI 文案未通过数值/决策一致性校验（"
                       + ai_review.describe() + "），已自动回退为规则化解读。")
        elif ai_review is not None and ai_review.notes:
            st.caption("AI 解读校验提示：" + "；".join(ai_review.notes))

        st.markdown("#### 📊 历史规则回测")
        if bt_res.metrics and bt_res.metrics.sample_size > 0:
            m = bt_res.metrics
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("样本计划数", m.sample_size)
            r2.metric("入场区命中率", f"{m.entry_zone_hit_rate:.0%}")
            r3.metric("止损触发率", f"{m.stop_loss_trigger_rate:.0%}")
            r4.metric("T1 命中率", f"{m.target_1_hit_rate:.0%}")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("胜率", f"{m.win_rate:.0%}", help="只统计真正成交的交易，未成交不计入")
            r2.metric("平均净收益", f"{m.avg_return:.2f}%",
                      help=f"已扣除佣金/印花税/滑点；扣费前 {m.gross_avg_return:.2f}%")
            r3.metric("平均持有", f"{m.avg_holding_period:.1f} 日")
            r4.metric("最大回撤(逐笔)", f"{m.max_drawdown:.2f}%")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("组合总收益", f"{m.portfolio_total_return:.2f}%",
                      help="按现金与持仓结算的可实现口径，消除重叠信号重复占用资金")
            r2.metric("组合最大回撤", f"{m.portfolio_max_drawdown:.2f}%")
            r3.metric("成本侵蚀/笔", f"{m.cost_drag:.2f}pp")
            r4.metric("信号重叠率", f"{m.overlap_rate:.0%}")
            st.caption(
                f"成交口径：限价成交需真实触及委托价；已计佣金/印花税/滑点；"
                f"止损按跳空价成交；一字涨跌停与停牌日不成交。"
                f"未成交 {m.not_entered} 笔，容量受限 {m.capacity_limited_rate:.0%}，"
                f"平均可成交比例 {m.avg_fill_ratio:.0%}。"
                "回测为历史规则有效性验证，不构成对未来表现的保证。"
            )
        else:
            st.info("样本不足，未生成回测。")
else:
    st.info("当前无推荐（网络/数据源问题），可下方『自定义扫描』输入代码")


# --------------------------------------------------------------------------- #
# 自定义扫描（备用：手动输入任意代码，市场自动识别）
# --------------------------------------------------------------------------- #
with st.expander("🛠 自定义扫描（手动输入任意代码）"):
    bcol1, bcol3 = st.columns([3, 1])
    batch_input = bcol1.text_input(
        "候选股票代码（逗号分隔）",
        "600000, 000001, 600519",
        key="custom_codes",
        help="逐个跑机会引擎，输出按机会分排序的交易计划（过滤 AVOID）",
    )
    batch_run = bcol3.button("扫描", type="secondary", use_container_width=True, key="custom_run")

    if batch_run:
        codes = [c.strip() for c in batch_input.replace("，", ",").split(",") if c.strip()]
        if not codes:
            st.warning("请输入至少一个股票代码")
        else:
            with st.spinner(f"⏳ 正在批量分析 {len(codes)} 只，请稍候..."):
                # 自定义扫描允许混市场：只有**全部为 A 股**时才套用上证市场状态，
                # 否则中性 —— 上证指数只对 A 股有效，套到港股/美股会误导评分与仓位。
                _all_cn = all(detect_market(c).market == "CN" for c in codes)
                eng = OpportunityEngine(
                    account_equity=ACCOUNT,
                    regime_score=(regime.score if regime else None) if _all_cn else None,
                    market_factor=(regime.factor if regime else 1.0) if _all_cn else 1.0,
                    fetch_news=True,
                )
                scanner = OpportunityBatchScanner(engine=eng, workers=5)
                custom_res = scanner.scan(codes)
            if not _all_cn:
                st.caption("候选含非 A 股标的：市场状态按中性处理（上证指数只对 A 股有效）。")
            if custom_res.plans:
                st.success(f"✔ 扫描完成：{len(custom_res.plans)} 个有效计划（AVOID 已过滤，耗时 {custom_res.elapsed:.1f}s）")
                rows = []
                for p in custom_res.plans:
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
                        "RR": f"1:{p.get('risk_reward_1')}",
                        "仓位%": p.get("position_percent"),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.warning("本轮无有效机会（可能全部 AVOID 或数据不足）")
            if custom_res.failed:
                with st.expander(f"⚠️ {len(custom_res.failed)} 只分析失败"):
                    for f in custom_res.failed:
                        st.write(f"- {f.get('name')}({f.get('code')}): {f.get('error')}")
            st.caption(f"耗时 {custom_res.elapsed:.1f}s")

render_disclaimer()
