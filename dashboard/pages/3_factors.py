# ruff: noqa: E402
"""因子验证看板 —— 让「回验」看得见，也让结论真的改变打分。

为什么单独开一页
----------------
``stock_analysis.research.factor_validation`` 能算出每个因子的 IC/IR、分组单调性与
滚动前瞻结论，但在此之前结论只落在 ``results/factor_validation_report.json``：
**没人看，也没东西读它。**

后果是两种典型失效：
1. 某个因子长期 IC≈0 甚至反向，仍按固定权重持续污染排序 —— 表现为「推荐的票怪怪的」；
2. 跑完验证看到「分组不单调」，但没有任何机制把它从公式里降下来，验证成了仪式。

本页把这条链路补全：
  报告 JSON → 可视化（IC/IR、分位收益、滚动前瞻、分层） → **自动降权结果** → 生效权重。

页面分四块
----------
1. **权重状态**：基线 vs 生效权重、是否套用、为什么。这是本页的核心 ——
   看完指标立刻能看到「所以公式变成了什么」。
2. **因子汇总**：IC/IR/t 值/顶底差/单调性 + 人话结论。
3. **分位收益**：能不能按这个因子排序选票（IC 说明有关系，单调性说明方向对不对）。
4. **稳健性**：滚动前瞻样本外 + 按决策分层。

Run::

    streamlit run quant_trading_system/dashboard/pages/3_factors.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import streamlit as st
from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.disclaimer import render_disclaimer
from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
from quant_trading_system.stock_analysis.scoring.composite import COMPOSITE_WEIGHTS
from quant_trading_system.stock_analysis.scoring.factor_weights import (
    DIMENSION_FACTOR,
    VERDICT_LABEL_ZH,
    active_weights,
    default_report_path,
)

apply_theme()
require_login()
page_header("因子验证", "IC/IR · 分组单调性 · 滚动前瞻 · 自动降权", "Factors")

_DIM_LABEL = {"stock": "质量分", "opportunity": "机会分", "rr": "赔率"}
_KIND_EMOJI = {
    "usable": "✅", "nonmonotone": "🟠", "unstable": "🟡",
    "useless": "⛔", "insufficient": "⚪", "unknown": "⚪",
}


@st.cache_data(ttl=300, show_spinner=False)
def _load_report(path_str: str, mtime: float) -> dict:
    """读报告 JSON。``mtime`` 进缓存键 → 报告一重写就自动重读。"""
    p = Path(path_str)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)}


def _report_path() -> Path:
    return default_report_path()


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return -1.0


path = _report_path()
report = _load_report(str(path), _mtime(path))
weights, wmeta = active_weights(use_cache=False)

# --------------------------------------------------------------------------- #
# 1) 权重状态（本页核心：验证结论 → 公式到底改成了什么）
# --------------------------------------------------------------------------- #
st.subheader("⚖️ 组合分权重（按验证结论自动降权）")

c1, c2 = st.columns([1.15, 1])
with c1:
    rows = []
    kinds = wmeta.get("kinds") or {}
    for dim, factor in DIMENSION_FACTOR.items():
        base = COMPOSITE_WEIGHTS.get(dim, 0.0)
        now = weights.get(dim, 0.0)
        kind = kinds.get(dim, "—")
        rows.append({
            "维度": _DIM_LABEL.get(dim, dim),
            "对应因子": factor,
            "基线权重": f"{base:.0%}",
            "生效权重": f"{now:.0%}",
            "变化": "—" if abs(now - base) < 1e-9 else f"{(now - base) * 100:+.1f}pp",
            "判定": f"{_KIND_EMOJI.get(kind, '')} {VERDICT_LABEL_ZH.get(kind, '未读取')}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with c2:
    if wmeta.get("applied"):
        st.success(f"**已按验证结论调整权重。** {wmeta.get('reason', '')}")
    else:
        st.info(f"**当前使用基线权重。** {wmeta.get('reason', '')}")
    if wmeta.get("age_days") is not None:
        st.caption(
            f"报告生成于 {wmeta['age_days']:.1f} 天前"
            f"（过期阈值 {wmeta.get('max_age_days', 30)} 天）"
        )
    st.caption(f"报告路径：`{wmeta.get('report_path', path)}`")

with st.expander("为什么是这些权重？逐条判定说明", expanded=bool(wmeta.get("applied"))):
    for n in (wmeta.get("notes") or ["（无说明）"]):
        st.markdown(f"- {n}")
    st.caption(
        "判定口径：**可用** 全额保留；**分组不单调** 降至 60%；**区分力不稳定** 降至 50%；"
        "**无区分力** 移出公式（0）；**样本不足 / 报告未覆盖** 不降权（没测出来 ≠ 测出来不行）。"
        "守卫：降权后若仅剩 1 个维度有权重，回退基线；单维度上限 60%。"
    )

st.divider()

# --------------------------------------------------------------------------- #
# 2) 报告状态
# --------------------------------------------------------------------------- #
if not path.exists():
    st.warning(
        "尚未产出因子验证报告，当前打分使用基线权重（这是安全的退化，不是错误）。\n\n"
        "**生成方法**（在项目根目录执行，二选一）："
    )
    st.code(
        "# 合成数据（离线，可入 CI，秒级）\n"
        "python examples/validate_factors.py --synthetic\n\n"
        "# 真实行情（联网拉多只 A 股，结论才有实际意义）\n"
        "python examples/validate_factors.py --codes 600519 000001 601398 000858 002594 601857",
        language="bash",
    )
    st.caption(
        "⚠️ 合成数据的结论只用于验证流程是否跑通，**不可**据此调权重 —— "
        "它没有真实的市场结构。真实调权重请务必用真实行情、并把股票池扩到 30 只以上。"
    )
    render_disclaimer()
    st.stop()

if report.get("_error"):
    st.error(f"报告无法解析：{report['_error']}")
    render_disclaimer()
    st.stop()

panel = report.get("panel") or {}
src = report.get("source") or "—"
gen = report.get("generated_at") or "（报告未记录生成时间）"
m1, m2, m3 = st.columns(3)
m1.metric("数据源", str(src))
m2.metric("成交样本", f"{panel.get('n_obs', '—')} 条")
m3.metric("有效交易日", f"{panel.get('n_dates', '—')} 个")
st.caption(f"生成时间：{gen} · 生成脚本：`{report.get('generated_by') or '—'}`")

if str(src).startswith("合成"):
    st.warning(
        "当前报告来自**合成数据**：仅用于验证链路是否跑通，"
        "其结论不具备调权重的效力。请用真实行情重跑后再据此判断。"
    )

summary = report.get("summary") or []
if not summary:
    st.info("报告里没有因子汇总（可能全部未成交或列类型不符）。")
    render_disclaimer()
    st.stop()

# --------------------------------------------------------------------------- #
# 3) 因子汇总
# --------------------------------------------------------------------------- #
st.subheader("📊 因子汇总（按 |IC 均值| 降序）")
sum_df = pd.DataFrame(summary)
for col in ("ic_mean", "ir", "t_stat", "ic_win_rate", "top_minus_bottom"):
    if col in sum_df.columns:
        sum_df[col] = pd.to_numeric(sum_df[col], errors="coerce")
show_cols = [c for c in (
    "factor", "n_obs", "n_periods", "ic_mean", "ir", "t_stat",
    "ic_win_rate", "top_minus_bottom", "monotone", "verdict",
) if c in sum_df.columns]
st.dataframe(
    sum_df[show_cols].rename(columns={
        "factor": "因子", "n_obs": "样本", "n_periods": "期数",
        "ic_mean": "IC 均值", "ir": "IR", "t_stat": "t 值",
        "ic_win_rate": "IC>0 占比", "top_minus_bottom": "顶底差",
        "monotone": "单调", "verdict": "结论",
    }),
    use_container_width=True, hide_index=True,
)
st.caption(
    "**怎么读**：IC 均值看**方向与量级**（正=因子越大越赚）；IR / t 值看稳定性 —— "
    "注意 stride 小于持有期时样本重叠会让 t 值被高估，别只看 t 值下结论；"
    "单调性看能否**按因子排序选票**；顶底差看最高分组比最低分组多赚多少。"
)

# --------------------------------------------------------------------------- #
# 4) 分位收益（单调性可视化）
# --------------------------------------------------------------------------- #
detail = report.get("detail") or {}
if detail:
    st.subheader("📈 分位平均收益（能否按此因子排序选票）")
    st.caption(
        "每个交易日内按因子等分 5 组、组内取收益均值，再跨期平均。"
        "**分位 1 = 因子最低，分位 5 = 因子最高**；柱子应从左到右单调升高（或降低）。"
        "柱形杂乱 = 排序含义模糊 = 不应据此排序。"
    )
    for factor, rep in detail.items():
        qs = (rep or {}).get("quantiles") or []
        if not qs:
            continue
        mono = (rep or {}).get("monotonicity") or {}
        qdf = pd.DataFrame(qs)
        qdf = qdf.rename(columns={"quantile": "分位", "mean_return": "平均收益%"})
        if "分位" in qdf.columns:
            qdf = qdf.sort_values("分位")
            qdf["分位"] = qdf["分位"].map(lambda q: f"Q{int(q)}")
        with st.expander(
            f"{factor} — {rep.get('verdict', '')}"
            f"（顶底差 {mono.get('top_minus_bottom', '—')}）",
            expanded=False,
        ):
            chart_df = qdf.set_index("分位")[["平均收益%"]]
            st.bar_chart(chart_df, use_container_width=True)
            st.dataframe(qdf, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- #
# 5) 稳健性：滚动前瞻 + 分层
# --------------------------------------------------------------------------- #
st.subheader("🧪 稳健性检验")
st.caption(
    "**滚动前瞻**：训练段（更早）→ 测试段（更晚），检查 IC 是否延续。"
    "样本内好看、样本外不延续的因子，等于没验证过。"
)
any_wf = False
for factor, rep in (detail or {}).items():
    wf = (rep or {}).get("walk_forward") or []
    if not wf:
        continue
    any_wf = True
    wdf = pd.DataFrame(wf)
    kept = int(pd.to_numeric(wdf.get("sign_kept"), errors="coerce").fillna(0).sum()) \
        if "sign_kept" in wdf.columns else 0
    with st.expander(f"{factor} — 样本外 {kept}/{len(wdf)} 折保持同号", expanded=False):
        cols = [c for c in ("fold", "train_periods", "test_periods", "train_ic",
                            "test_ic", "train_ir", "test_ir", "sign_kept")
                if c in wdf.columns]
        st.dataframe(
            wdf[cols].rename(columns={
                "fold": "折", "train_periods": "训练期数", "test_periods": "测试期数",
                "train_ic": "训练 IC", "test_ic": "测试 IC",
                "train_ir": "训练 IR", "test_ir": "测试 IR", "sign_kept": "同号延续",
            }),
            use_container_width=True, hide_index=True,
        )
if not any_wf:
    st.info("报告里没有滚动前瞻结果（样本期数不足或未计算）。")

any_strat = False
for factor, rep in (detail or {}).items():
    strat = (rep or {}).get("by_decision") or []
    if not strat or len(strat) < 2:
        continue
    any_strat = True
    with st.expander(f"{factor} — 按决策类型分层 IC", expanded=False):
        st.dataframe(
            pd.DataFrame(strat).rename(columns={
                "regime": "决策", "n_obs": "样本", "n_periods": "期数",
                "ic_mean": "IC 均值", "ir": "IR", "t_stat": "t 值",
                "ic_win_rate": "IC>0 占比",
            }),
            use_container_width=True, hide_index=True,
        )
        st.caption("只在某一类信号里有效 = 该因子不宜全样本通用，应考虑分场景使用。")
if not any_strat:
    st.caption("（无分层结果：报告未含 decision 列或分组不足。）")

# --------------------------------------------------------------------------- #
# 6) 操作
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("🔁 重新验证")
st.code(
    "# 真实行情（推荐：股票池 ≥30 只，结论才有统计意义）\n"
    "python examples/validate_factors.py --codes <代码列表> --days 900 --stride 20\n\n"
    "# 离线自检（验证链路是否跑通，不可用于调权重）\n"
    "python examples/validate_factors.py --synthetic",
    language="bash",
)
if st.button("刷新（清除权重缓存并重读报告）", key="reload_factor"):
    st.cache_data.clear()
    from quant_trading_system.stock_analysis.scoring.factor_weights import clear_cache
    clear_cache()
    st.rerun()
st.caption(
    "报告更新后权重会在 5 分钟内自动生效（缓存以文件修改时间为键），"
    "无需重启应用；点上面按钮可立即生效。"
)

render_disclaimer()
