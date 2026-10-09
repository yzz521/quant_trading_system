"""推荐质量闸门 —— 让「买」这个动作必须先过质量与数据关卡。

背景
----
审计发现：``build_trading_plan`` 的 BUY/AVOID 只取决于**风险收益比**和「现价是否
落在入场区间」，``stock_score`` / ``opportunity_score`` 完全不参与决策。批量扫描
按机会分排序、机会分下限默认 0，而机会分里技术类因子权重最高。结果是：基本面差、
估值离谱、盈利下滑的股票，只要技术形态能生成一个好看的 RR，就会被标成 BUY_NOW。

更隐蔽的问题是**数据缺失被中性化**：成长缺失给 50 分、PE 缺失给 50 分、板块缺失给
50 分。中性补分让「没数据」和「数据正常」在分数上无法区分，9 因子里的基本面/成长/
估值三个维度在批量推荐链路里因此近乎失效。

本模块把这些隐性规则变成**显式、可审计、可降级**的闸门：

  * ``compute_coverage`` —— 说清哪些字段真的拿到了。
  * ``QualityGateConfig`` —— 门槛全部可配置，默认值面向 A 股实盘。
  * ``evaluate`` —— 输出 BUY / WATCH / REJECT 三档 + 逐条不通过原因。

设计原则
--------
1. **缺失 ≠ 中性**：关键字段缺失只降级、不加分。
2. **不通过必须给理由**：每一条被拦下的规则都写进 ``reasons``，看板与 AI 直接展示。
3. **回测可放宽数据要求**：历史链路拿不到财务数据，用
   ``QualityGateConfig.for_backtest()`` 放宽「数据完整性」类规则，但保留分数与 RR
   门槛 —— 否则回测会因为「没有历史财报」而一笔都不成交。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CORE_FIELDS = ("pe", "total_cap_yi", "turnover")
QUALITY_FIELDS = ("roe", "profit_yoy", "rev_yoy")
FLOW_FIELDS = ("main_net",)
ALL_FIELDS = CORE_FIELDS + QUALITY_FIELDS + FLOW_FIELDS

TIER_BUY = "BUY"
TIER_WATCH = "WATCH"
TIER_REJECT = "REJECT"


def _is_number(v) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return f == f  # 排除 NaN
    except (TypeError, ValueError):
        return False


@dataclass
class DataCoverage:
    """数据完整度：说清「这只票的数据到底拿到了多少」。"""

    present: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    n_present: int = 0
    n_total: int = 0
    ratio: float = 0.0
    has_core: bool = False      # 估值/市值/换手（快照即可得）
    has_quality: bool = False   # ROE 或 营收/净利同比（需财务接口）

    def to_dict(self) -> dict:
        return {
            "ratio": round(self.ratio, 3),
            "n_present": self.n_present,
            "n_total": self.n_total,
            "has_core": self.has_core,
            "has_quality": self.has_quality,
            "missing": list(self.missing),
            "present": dict(self.present),
        }

    @property
    def summary(self) -> str:
        return f"{self.n_present}/{self.n_total} 字段可用" + (
            f"（缺：{'、'.join(self.missing)}）" if self.missing else ""
        )


def compute_coverage(extra: Optional[dict]) -> DataCoverage:
    """统计关键字段的可用情况。"""
    extra = extra or {}
    present: dict = {}
    missing: list = []
    for k in ALL_FIELDS:
        ok = _is_number(extra.get(k))
        present[k] = ok
        if not ok:
            missing.append(k)
    n_present = sum(1 for v in present.values() if v)
    n_total = len(ALL_FIELDS)
    return DataCoverage(
        present=present,
        missing=missing,
        n_present=n_present,
        n_total=n_total,
        ratio=n_present / n_total if n_total else 0.0,
        has_core=all(present.get(k) for k in CORE_FIELDS),
        has_quality=any(present.get(k) for k in QUALITY_FIELDS),
    )


@dataclass
class QualityGateConfig:
    """质量闸门配置。

    Attributes:
        enabled: 关闭时 ``evaluate`` 恒返回 BUY（用于对照实验 / 老行为兼容）。
        min_stock_score: 个股质量分下限。
        min_opportunity_score: 机会分下限。
        min_rr: 风险收益比下限（低于此值不允许买入）。
        min_data_coverage: 关键字段覆盖率下限。
        require_quality_data: 是否要求至少拿到一项盈利/成长数据（ROE 或同比）。
        min_total_cap_yi / max_total_cap_yi: 总市值区间（亿元），剔除微盘与超大盘。
        min_amount: 当日最低成交额（元），保证可交易性。
        max_pe / allow_negative_pe: 估值上限与是否允许亏损股。
        min_risk_component: 风险维度分下限（0-100，越高越安全）。
        block_severe_news: 命中重大风险公告/新闻时是否硬否决。
        max_new_positions_hint: 组合层提示（仅用于文案，不在此处强制）。
    """

    enabled: bool = True
    min_stock_score: float = 60.0
    min_opportunity_score: float = 65.0
    min_rr: float = 2.0
    min_data_coverage: float = 0.4
    require_quality_data: bool = True
    min_total_cap_yi: float = 20.0
    max_total_cap_yi: float = 20000.0
    min_amount: float = 1e7
    max_pe: float = 150.0
    allow_negative_pe: bool = False
    min_risk_component: float = 45.0
    block_severe_news: bool = True

    @classmethod
    def for_backtest(cls) -> "QualityGateConfig":
        """回测用配置：放宽「数据完整性」类规则，保留分数与 RR 门槛。

        历史链路（``TradingPlanBacktest``）不拉财务与资金流数据，若沿用实盘配置会
        因为「没有历史财报」而一笔都不成交。这里只放宽数据类规则，
        ``min_stock_score`` / ``min_opportunity_score`` / ``min_rr`` 仍然生效 ——
        这样回测验证的正是「闸门 + 价格规则」的整体效果。
        """
        return cls(
            enabled=True,
            min_stock_score=55.0,
            min_opportunity_score=60.0,
            min_rr=2.0,
            min_data_coverage=0.0,
            require_quality_data=False,
            min_total_cap_yi=0.0,
            max_total_cap_yi=float("inf"),
            min_amount=0.0,
            max_pe=float("inf"),
            allow_negative_pe=True,
            min_risk_component=0.0,
            block_severe_news=False,
        )

    @classmethod
    def disabled(cls) -> "QualityGateConfig":
        """完全关闭（仅用于 A/B 对照，不要在生产路径使用）。"""
        return cls(enabled=False)


@dataclass
class QualityGateResult:
    """闸门结论。"""

    passed: bool = True
    tier: str = TIER_BUY
    failed: list = field(default_factory=list)     # 规则名
    reasons: list = field(default_factory=list)    # 人话（可直接展示）
    coverage: Optional[DataCoverage] = None
    checks: dict = field(default_factory=dict)     # 逐条明细，便于审计

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "tier": self.tier,
            "failed": list(self.failed),
            "reasons": list(self.reasons),
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "checks": self.checks,
        }

    @property
    def summary(self) -> str:
        if self.passed:
            cov = self.coverage.summary if self.coverage else ""
            return f"通过质量闸门（{cov}）" if cov else "通过质量闸门"
        return "未通过质量闸门：" + "；".join(self.reasons)


def evaluate(
    *,
    stock_score: Optional[float],
    opportunity_score: Optional[float],
    risk_reward_1: Optional[float],
    extra: Optional[dict] = None,
    risk_component: Optional[float] = None,
    severe_news: bool = False,
    amount: Optional[float] = None,
    config: Optional[QualityGateConfig] = None,
) -> QualityGateResult:
    """执行质量闸门判定。

    Args:
        stock_score: 个股质量分（0-100）。
        opportunity_score: 机会分（0-100）。
        risk_reward_1: 风险收益比（T1）。
        extra: 外部数据（PE/市值/换手/ROE/同比/资金流）。
        risk_component: 9 因子中 risk 维度的分数（越高越安全）。
        severe_news: 是否命中重大风险公告/新闻。
        amount: 当日成交额（元）。
        config: 闸门配置；None 时用实盘默认。

    Returns:
        ``QualityGateResult``。``tier`` 为 BUY / WATCH / REJECT：
          * REJECT —— 硬否决（重大风险、亏损股、估值离谱、成交额过低）
          * WATCH  —— 软性不达标（分数不够、数据不全、RR 偏低）
          * BUY    —— 全部通过
    """
    cfg = config or QualityGateConfig()
    cov = compute_coverage(extra)
    if not cfg.enabled:
        return QualityGateResult(passed=True, tier=TIER_BUY, coverage=cov,
                                 checks={"enabled": False})

    extra = extra or {}
    hard: list[str] = []    # 硬否决原因
    soft: list[str] = []    # 降级原因
    checks: dict = {}

    def _record(name: str, ok: bool, fail_msg: str, *,
                ok_msg: str = "", hard_block: bool = False) -> None:
        """记录一条检查。

        ``fail_msg`` 只在**不通过**时展示（也只在此时进 ``reasons``）。
        通过时展示 ``ok_msg`` —— 原实现无论通过与否都写同一句「…低于下限…」，
        审计时会把「通过」读成「不通过」，是纯粹的自伤。
        """
        checks[name] = {
            "ok": bool(ok),
            "msg": fail_msg if not ok else (ok_msg or fail_msg),
            "hard": bool(hard_block),
        }
        if not ok:
            (hard if hard_block else soft).append(fail_msg)

    # ---- 硬否决项 ----
    if cfg.block_severe_news and severe_news:
        _record("severe_news", False, "命中重大风险公告/新闻（立案/调查/处罚等）",
                ok_msg="未命中重大风险公告/新闻", hard_block=True)

    pe = extra.get("pe")
    if _is_number(pe):
        pev = float(pe)
        if pev <= 0 and not cfg.allow_negative_pe:
            _record("profitability", False, f"公司当前亏损（PE={pev:.1f}）", hard_block=True)
        elif pev > cfg.max_pe:
            _record("valuation", False,
                    f"估值过高（PE={pev:.1f} > 上限 {cfg.max_pe:.0f}）", hard_block=True)
        else:
            _record("valuation", True, "", ok_msg=f"PE={pev:.1f} 在允许区间")
    else:
        _record("valuation", True, "", ok_msg="PE 缺失，估值项未参与硬否决")

    if amount is not None and cfg.min_amount > 0 and _is_number(amount):
        amt = float(amount)
        _record("liquidity", amt >= cfg.min_amount,
                f"成交额 {amt/1e8:.2f} 亿低于下限 {cfg.min_amount/1e8:.2f} 亿",
                ok_msg=f"成交额 {amt/1e8:.2f} 亿达标（下限 {cfg.min_amount/1e8:.2f} 亿）",
                hard_block=True)

    cap = extra.get("total_cap_yi")
    if _is_number(cap) and (cfg.min_total_cap_yi > 0 or cfg.max_total_cap_yi > 0):
        capv = float(cap)
        if cfg.min_total_cap_yi > 0 and capv < cfg.min_total_cap_yi:
            _record("market_cap", False,
                    f"总市值 {capv:.0f} 亿低于下限 {cfg.min_total_cap_yi:.0f} 亿",
                    hard_block=True)
        elif cfg.max_total_cap_yi > 0 and capv > cfg.max_total_cap_yi:
            # 原实现只查下限、从不查上限 —— ``max_total_cap_yi`` 是个摆设。
            _record("market_cap", False,
                    f"总市值 {capv:.0f} 亿超过上限 {cfg.max_total_cap_yi:.0f} 亿",
                    hard_block=True)
        else:
            _record("market_cap", True, "",
                    ok_msg=f"总市值 {capv:.0f} 亿在允许区间"
                           f"（{cfg.min_total_cap_yi:.0f}~{cfg.max_total_cap_yi:.0f} 亿）")

    if risk_component is not None and cfg.min_risk_component > 0:
        rc = float(risk_component)
        _record("risk_score", rc >= cfg.min_risk_component,
                f"风险维度分 {rc:.0f} 低于下限 {cfg.min_risk_component:.0f}",
                ok_msg=f"风险维度分 {rc:.0f} 达标（下限 {cfg.min_risk_component:.0f}）")

    # ---- 软性降级项 ----
    if stock_score is not None and cfg.min_stock_score > 0:
        ss = float(stock_score)
        _record("stock_score", ss >= cfg.min_stock_score,
                f"个股质量分 {ss:.0f} < 门槛 {cfg.min_stock_score:.0f}",
                ok_msg=f"个股质量分 {ss:.0f} 达标（门槛 {cfg.min_stock_score:.0f}）")

    if opportunity_score is not None and cfg.min_opportunity_score > 0:
        os_ = float(opportunity_score)
        _record("opportunity_score", os_ >= cfg.min_opportunity_score,
                f"机会分 {os_:.0f} < 门槛 {cfg.min_opportunity_score:.0f}",
                ok_msg=f"机会分 {os_:.0f} 达标（门槛 {cfg.min_opportunity_score:.0f}）")

    if cfg.min_rr > 0:
        if risk_reward_1 is None:
            # 赔率缺失（入场/止损/目标参数不全，或止损距离低于可交易下限）
            # ⇒ 无法评估。原实现直接跳过检查 = **放行**，等于「测不出来就当通过」；
            # 更糟的是它恰好放过止损贴着入场价的计划 —— 那类计划的 RR 被数学
            # 放大成几十上百倍，本该是最该拦下的（见 risk_reward.MIN_RISK_PCT）。
            _record("risk_reward", False,
                    "无法评估赔率（风险收益比缺失：参数不全，或止损距离低于交易成本下限）")
        else:
            rr = float(risk_reward_1)
            _record("risk_reward", rr >= cfg.min_rr,
                    f"风险收益比 1:{rr:.2f} < 门槛 1:{cfg.min_rr:.2f}",
                    ok_msg=f"风险收益比 1:{rr:.2f} 达标（门槛 1:{cfg.min_rr:.2f}）")

    if cfg.min_data_coverage > 0:
        _record("data_coverage", cov.ratio >= cfg.min_data_coverage,
                f"关键数据覆盖率 {cov.ratio:.0%} < 门槛 {cfg.min_data_coverage:.0%}"
                f"（缺：{'、'.join(cov.missing) or '无'}）",
                ok_msg=f"关键数据覆盖率 {cov.ratio:.0%} 达标")

    if cfg.require_quality_data:
        _record("quality_data", cov.has_quality,
                "缺少盈利/成长数据（ROE、营收或净利同比），无法判断盈利质量",
                ok_msg="盈利/成长数据齐备")

    # ---- 结论 ----
    if hard:
        tier = TIER_REJECT
    elif soft:
        tier = TIER_WATCH
    else:
        tier = TIER_BUY

    return QualityGateResult(
        passed=(tier == TIER_BUY),
        tier=tier,
        failed=hard + soft,
        reasons=hard + soft,
        coverage=cov,
        checks=checks,
    )
