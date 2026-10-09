"""批量机会扫描器 —— 一批候选股 → 各自的交易计划。

数据流（计划书 §18 每日运行时流程）：
  候选股列表 → 逐票拉K线（+ 财务/估值字段）→ OpportunityEngine.analyze → 交易计划
并发拉取（默认 5 workers），单票失败不影响整体，输出按**组合分**排序、
过滤掉 AVOID / 数据不足的标的。可直接对接邮件模板 trading_plans 参数。

2026-09 的三处修正
------------------
1. **候选字段透传**：初筛快照里已经有 PE / 市值 / 换手 / 成交额，原实现全部丢弃，
   导致批量路径的估值、基本面维度只能拿「缺失 → 中性 50 分」。现在随候选一起
   传给引擎（``extra``），质量闸门也才有数据可判。
2. **排序改用组合分**：原实现只按 ``opportunity_score`` 排序，而机会分不含任何
   公司质量成分。现在用 ``scoring.composite``（质量 45% + 时机 35% + 赔率 20%），
   并支持 ``min_stock_score`` 下限过滤。
3. **财务数据可选预取**：仅走**线程安全**的同花顺官方接口（纯 HTTP）。未配置该
   接口时不回退 akshare（mini_racer 在并发下会崩），而是把闸门的
   ``require_quality_data`` 放宽，并把「本次未获取财务数据」写进结果，避免
   用户以为质量关已经严格生效。
"""
from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from ...utils import get_logger
from ..data_fetcher import detect_market, fetch_kline_hk_tencent, fetch_kline_sina_api
from ..indicators import add_all_indicators
from ..scoring.composite import COMPOSITE_WEIGHTS, score_plan
from ..scoring.factor_weights import active_weights
from .opportunity_engine import OpportunityEngine
from .quality_gate import QualityGateConfig
from .trading_plan import DecisionState

log = get_logger("OpportunityBatch")

# 默认数据加载器：code → 已加指标的日K DataFrame（失败返回 None）
KlineLoader = Callable[[str, str], Optional[pd.DataFrame]]

# 从候选快照透传给引擎的字段（质量闸门与 9 因子直接消费）
_EXTRA_KEYS = ("pe", "total_cap_yi", "float_cap_yi", "turnover", "pb", "amount", "industry")


def _default_loader(code: str, market: str = "CN") -> Optional[pd.DataFrame]:
    """默认数据加载器：按市场选线程安全的 K 线源。

    * CN：新浪日K JSON 接口（纯 urllib，线程安全）
    * US：yfinance（线程安全）
    * HK：腾讯 hkfqkline（纯 urllib，线程安全）
    批量扫描是并发的，绝不能走 akshare 的 stock_zh_a_daily / stock_hk_daily ——
    其内置 mini_racer JS 引擎非线程安全，多线程并发必崩
    （libmini_racer address_pool_manager Check failed）。
    """
    try:
        info = detect_market(code)
        if info.market == "CN":
            raw = fetch_kline_sina_api(info, days=250)
        elif info.market == "HK":
            raw = fetch_kline_hk_tencent(info, days=250)
        else:  # US
            from ..data_fetcher import fetch_kline

            raw = fetch_kline(info, days=250)
        if raw is None or raw.empty:
            return None
        return add_all_indicators(raw)
    except Exception as e:  # noqa: BLE001
        log.debug("拉取 %s K线失败: %s", code, e)
        return None


def fundamentals_available() -> bool:
    """是否具备**线程安全**的财务数据源（同花顺官方接口）。

    注意：这只回答「数据源**配好了**吗」，**不**回答「本轮**取到**了吗」。
    接口配置了但返回空时它照样为 True —— 需要「本轮真实结果」请用
    ``BatchScanResult.fundamentals_available``（按实际取到的质量字段判定）。

    未配置时批量扫描不预取财务数据，闸门的盈利质量要求会被显式放宽
    （见 ``OpportunityBatchScanner._effective_gate``）。
    """
    try:
        from .. import hithink

        return bool(hithink.is_enabled())
    except Exception:  # noqa: BLE001
        return False


def _fetch_spot_snapshot(codes: list[str]) -> dict[str, dict]:
    """批量拉快照字段（PE / 总市值 / 换手率），一次请求约 50 只。

    为什么必须有这一步：闸门的 ``min_data_coverage`` 是拿 ``ALL_FIELDS``
    （3 个 CORE + 3 个 QUALITY + 1 个 FLOW）算比例，门槛 40% ⇒ 至少 3 个字段。
    财务接口只给 ``profit_yoy`` / ``rev_yoy`` 两个，**凑不满 3 个** ——
    缺了 CORE 三件套，覆盖率恒为 2/7≈29%，闸门必然降级、BUY_NOW 通过率恒为 0。

    看板路径是手工补这三个字段的（``dashboard/pages/0_opportunity.py``），
    批量链路（邮件/调度器）一直漏了 —— 两条路径的闸门严格度因此并不一致。

    失败返回 ``{}``（静默降级，不阻断扫描）。
    """
    if not codes:
        return {}
    try:
        from ..data_fetcher import fetch_tencent_quotes

        df = fetch_tencent_quotes(list(codes))
        if df is None or len(df) == 0:
            return {}
        out: dict[str, dict] = {}
        for _, row in df.iterrows():
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            snap = {k: row.get(k) for k in ("pe", "total_cap_yi", "turnover")
                    if row.get(k) is not None}
            if snap:
                out[code] = snap
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("快照预取失败（闸门将因缺 CORE 字段而降级）: %s", e)
        return {}


def _fetch_fundamentals_threadsafe(code: str, market: str) -> dict:
    """线程安全的财务数据预取（当前仅 A 股 + 同花顺官方接口）。"""
    if str(market).upper() != "CN":
        return {}
    try:
        from .. import hithink

        if not hithink.is_enabled():
            return {}
        g = hithink.fetch_growth_cn(code)
        return {k: v for k, v in (g or {}).items() if v is not None}
    except Exception as e:  # noqa: BLE001
        log.debug("预取 %s 财务数据失败: %s", code, e)
        return {}


def _engine_accepts_extra(engine) -> bool:
    """引擎的 ``analyze`` 是否接受 ``extra`` 关键字（兼容测试替身）。"""
    try:
        params = inspect.signature(engine.analyze).parameters
    except (TypeError, ValueError):
        return False
    return "extra" in params


@dataclass
class BatchScanItem:
    """单票批量扫描结果（含原始计划，便于审计）。"""

    code: str = ""
    name: str = ""
    plan: Optional[dict] = None
    error: str = ""
    composite_score: float = 0.0
    extra: dict = field(default_factory=dict)
    gate_tier: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class BatchScanResult:
    """批量扫描结果：成功列表 + 失败列表。"""

    plans: list = field(default_factory=list)   # 过滤后的交易计划 dict（供邮件/看板）
    items: list = field(default_factory=list)   # 全部单项（含 AVOID）
    failed: list = field(default_factory=list)  # 失败项（code + error）
    elapsed: float = 0.0
    fundamentals_available: bool = False        # 本轮是否取到了财务数据
    gate_note: str = ""                         # 闸门口径说明（未取到财务数据时非空）

    def to_dict(self) -> dict:
        return {
            "plans": self.plans,
            "items": [i.to_dict() for i in self.items],
            "failed": self.failed,
            "elapsed": round(self.elapsed, 2),
            "fundamentals_available": self.fundamentals_available,
            "gate_note": self.gate_note,
        }


class OpportunityBatchScanner:
    """批量机会扫描器。

    Args:
        engine: 机会引擎（复用账户/风控配置）；None 时用默认。
        loader: code → df 加载器；默认联网拉 250 日K并加指标。
        workers: 并发数（A股行情接口对并发敏感，默认 5）。
        min_opportunity_score: 机会分下限（过滤弱机会）。
        min_stock_score: 个股质量分下限（0 = 不过滤）。
        sort_key: ``composite``（默认，质量+时机+赔率）或 ``opportunity``（旧口径）。
        include_avoid: 是否保留 AVOID 到 items（plans 恒过滤 AVOID）。
        fetch_fundamentals: 是否预取财务数据（仅线程安全源；默认开启）。
        gate: 覆盖引擎的质量闸门配置（None = 用引擎的）。
    """

    def __init__(
        self,
        engine: Optional[OpportunityEngine] = None,
        loader: Optional[KlineLoader] = None,
        *,
        workers: int = 5,
        min_opportunity_score: float = 0.0,
        min_stock_score: float = 0.0,
        sort_key: str = "composite",
        include_avoid: bool = True,
        fetch_fundamentals: bool = True,
        gate: Optional[QualityGateConfig] = None,
        weights: Optional[dict] = None,
        use_validated_weights: bool = True,
    ) -> None:
        self.engine = engine or OpportunityEngine()
        self.loader = loader or _default_loader
        self.workers = max(1, workers)
        self.min_opportunity_score = min_opportunity_score
        self.min_stock_score = min_stock_score
        if sort_key not in ("composite", "opportunity"):
            raise ValueError(f"未知 sort_key: {sort_key!r}")
        self.sort_key = sort_key
        self.include_avoid = include_avoid
        self.fetch_fundamentals = fetch_fundamentals
        self.gate = gate
        # 组合分权重：显式传入优先；否则按因子验证结论自动降权（见 factor_weights）。
        self._explicit_weights = weights
        self.use_validated_weights = use_validated_weights
        self.weights_meta: dict = {}
        self.weights: dict = self._resolve_weights()

    # ------------------------------------------------------------------ #
    def _resolve_weights(self) -> dict:
        """确定本轮排序用的组合分权重，并把来源写进 ``weights_meta``。

        * 显式传入 ``weights`` → 直接用，``applied=False``；
        * ``use_validated_weights=False`` → 基线权重（便于对照实验）；
        * 否则读 ``results/factor_validation_report.json``，按验证结论自动降权。
          报告缺失 / 过期 / 结论不触发降权时都会**优雅退化到基线**，并留下说明。
        """
        if self._explicit_weights is not None:
            self.weights_meta = {
                "source": "explicit",
                "applied": True,
                "reason": "调用方显式指定权重。",
                "notes": [],
                "kinds": {},
            }
            return {**COMPOSITE_WEIGHTS, **self._explicit_weights}
        if not self.use_validated_weights:
            self.weights_meta = {
                "source": "baseline",
                "applied": False,
                "reason": "已关闭「按验证结论自动降权」，使用基线权重。",
                "notes": [],
                "kinds": {},
            }
            return dict(COMPOSITE_WEIGHTS)
        try:
            w, meta = active_weights()
        except Exception as e:  # noqa: BLE001
            # 研究产物出问题绝不能拖垮打分链路：退化到基线并如实记录。
            self.weights_meta = {
                "source": "baseline",
                "applied": False,
                "reason": f"读取因子验证报告失败（{e}）→ 沿用基线权重。",
                "notes": [],
                "kinds": {},
            }
            return dict(COMPOSITE_WEIGHTS)
        self.weights_meta = {**meta, "source": "validated"}
        return dict(w)

    # ------------------------------------------------------------------ #
    def _effective_gate(self) -> tuple[Optional[QualityGateConfig], bool, str]:
        """确定本轮实际生效的闸门配置。

        拿不到财务数据时把 ``require_quality_data`` 放宽，并返回说明文案 ——
        这样「质量关到底严不严」是显式可见的，而不是默默失效。
        """
        base = self.gate if self.gate is not None else getattr(self.engine, "quality_gate", None)
        if base is None or not base.enabled:
            return base, False, ""
        has_fund = fundamentals_available() if self.fetch_fundamentals else False
        if has_fund or not base.require_quality_data:
            return base, has_fund, ""
        relaxed = QualityGateConfig(**{**base.__dict__, "require_quality_data": False})
        note = ("本次扫描未获取到财务数据（未配置同花顺官方数据源），"
                "质量闸门已放宽「盈利/成长数据」要求；个股质量分的成长维度为中性值。"
                "配置 config/hithink.env 后可启用完整质量关。")
        return relaxed, False, note

    # ------------------------------------------------------------------ #
    def scan(
        self,
        candidates: list,
        *,
        market: str = "CN",
        name_map: Optional[dict] = None,
    ) -> BatchScanResult:
        """对候选列表执行批量机会扫描。

        Args:
            candidates: 股票代码列表，或 [{"code":..., "name":...}] / ScanHit dict。
            market: 市场（CN/HK/US），用于诊断失败时的名称兜底。
            name_map: code → 名称 映射（优先级最高）。
        """
        import time

        codes = self._normalize(candidates, name_map)
        if not codes:
            return BatchScanResult()

        gate, has_fund, gate_note = self._effective_gate()
        if gate is not None and hasattr(self.engine, "quality_gate"):
            self.engine.quality_gate = gate

        # 闸门要求「关键数据覆盖率」时，先把 CORE 三件套（PE/总市值/换手）
        # 批量补上 —— 缺了它们覆盖率一定过不了门槛，整批会被降级成 WATCH。
        if gate is not None and getattr(gate, "min_data_coverage", 0) > 0:
            snapshot = _fetch_spot_snapshot([c["code"] for c in codes])
            for c in codes:
                for k, v in (snapshot.get(c["code"]) or {}).items():
                    c.setdefault(k, v)

        pass_extra = _engine_accepts_extra(self.engine)
        t0 = time.time()
        items: list[BatchScanItem] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {
                ex.submit(self._analyze_one, c, name_map, market, pass_extra): c["code"]
                for c in codes
            }
            for fut in as_completed(futs):
                try:
                    items.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    code = futs[fut]
                    log.debug("批量分析 %s 异常: %s", code, e)
                    items.append(BatchScanItem(code=code, name=name_map.get(code, code), error=str(e)))

        items.sort(key=self._sort_key_of)

        plans = []
        for it in items:
            p = it.plan
            if not p:
                continue
            if p.get("decision") == DecisionState.AVOID.value:
                continue
            if p.get("opportunity_score") is not None and p["opportunity_score"] < self.min_opportunity_score:
                continue
            if self.min_stock_score and (p.get("stock_score") or 0) < self.min_stock_score:
                continue
            plans.append(p)

        failed = [{"code": it.code, "name": it.name, "error": it.error} for it in items if it.error]
        # 标志位必须反映**本轮实际结果**，而不是「数据源配好了」——
        # 后者会让「一个字段都没取到」被读成「数据齐全」，闸门看着在把关、
        # 实则整批静默降级。
        fetched = self._quality_fields_fetched(items) if has_fund else False
        if has_fund and not fetched:
            gate_note = ("已配置同花顺财务数据源，但本轮未取到任何质量类字段"
                         "（ROE / 营收同比 / 净利同比）——闸门会因覆盖率不足而降级，"
                         "请检查数据源是否可用。")
        return BatchScanResult(
            plans=plans,
            items=items if self.include_avoid else [i for i in items if i.plan and i.plan.get("decision") != DecisionState.AVOID.value],
            failed=failed,
            elapsed=time.time() - t0,
            fundamentals_available=fetched,
            gate_note=gate_note,
        )

    @staticmethod
    def _quality_fields_fetched(items: list) -> bool:
        """本轮是否**真的**取到过质量类财务字段（ROE / 营收 / 净利同比）。

        ``fundamentals_available()`` 只看同花顺接口有没有启用，配置了但一个
        字段都没返回时它照样为 True。这里按扫描结果里的 ``extra`` 说话。
        """
        from .quality_gate import QUALITY_FIELDS

        def _num(v) -> bool:
            try:
                f = float(v)
                return f == f          # 排除 NaN
            except (TypeError, ValueError):
                return False

        for it in items:
            extra = getattr(it, "extra", None) or {}
            if any(_num(extra.get(k)) for k in QUALITY_FIELDS):
                return True
        return False

    def _sort_key_of(self, it: "BatchScanItem") -> tuple:
        """排序键：组合分降序（或旧口径机会分），并以代码做确定性 tie-break。"""
        if self.sort_key == "opportunity":
            primary = (it.plan or {}).get("opportunity_score") or 0.0
        else:
            primary = it.composite_score
        return (-float(primary), it.code)

    # ------------------------------------------------------------------ #
    def _normalize(self, candidates: list, name_map: Optional[dict]) -> list[dict]:
        """把混合输入归一为 [{code, name, ...透传字段}]。"""
        out: list[dict] = []
        for c in candidates:
            if isinstance(c, str):
                item = {"code": c, "name": (name_map or {}).get(c, c)}
            elif isinstance(c, dict):
                code = str(c.get("code") or c.get("symbol") or "").strip()
                item = {"code": code, "name": str(c.get("name") or (name_map or {}).get(code, code))}
                # 透传快照字段（PE/市值/换手/成交额），供评分与质量闸门使用
                for k in _EXTRA_KEYS:
                    v = c.get(k)
                    if v is not None and v == v:
                        item[k] = v
            else:
                continue
            if item["code"]:
                out.append(item)
        # 去重保序
        seen, dedup = set(), []
        for c in out:
            if c["code"] not in seen:
                seen.add(c["code"])
                dedup.append(c)
        return self._fill_names(dedup)

    # ------------------------------------------------------------------ #
    _NAME_TABLE: Optional[dict] = None   # code → 名称（A 股全量表，24h 缓存）
    _NAME_TABLE_TS: float = 0.0

    def _fill_names(self, codes: list[dict]) -> list[dict]:
        """A 股代码缺名称时用全量代码表补齐（一次拉取，24h 缓存）。

        批量扫描候选多为纯代码（str），不补名称则邮件/看板只显示代码。
        失败/非 A 股保持原样（用 code 兜底），不抛异常。
        """
        missing = [c for c in codes if not c.get("name") or c["name"] == c["code"]]
        cn_missing = [c for c in missing
                      if c["code"].isdigit() and len(c["code"]) == 6]
        if not cn_missing:
            return codes

        import time as _t

        if OpportunityBatchScanner._NAME_TABLE is None or \
                _t.time() - OpportunityBatchScanner._NAME_TABLE_TS > 24 * 3600:
            table: dict = {}
            try:
                import akshare as ak
                df = ak.stock_info_a_code_name()
                for _, r in df.iterrows():
                    table[str(r["code"])] = str(r["name"])
            except Exception as e:  # noqa: BLE001
                log.debug("A 股代码表获取失败（名称保持 code 兜底）: %s", e)
            OpportunityBatchScanner._NAME_TABLE = table
            OpportunityBatchScanner._NAME_TABLE_TS = _t.time()

        table = OpportunityBatchScanner._NAME_TABLE or {}
        for c in cn_missing:
            n = table.get(c["code"])
            if n:
                c["name"] = n
        return codes

    def _analyze_one(
        self, cand: dict, name_map: Optional[dict], market: str = "CN",
        pass_extra: bool = True,
    ) -> BatchScanItem:
        code, name = cand["code"], cand["name"]
        try:
            df = self.loader(code, name)
            if df is None or len(df) < 60:
                return BatchScanItem(code=code, name=name, error="数据不足")
            extra = {k: cand[k] for k in _EXTRA_KEYS if k in cand and cand[k] is not None}
            if self.fetch_fundamentals:
                for k, v in _fetch_fundamentals_threadsafe(code, market).items():
                    extra.setdefault(k, v)
            extra = {k: v for k, v in extra.items() if v is not None}
            res = self.engine.analyze(code, name, df, extra=extra) if pass_extra \
                else self.engine.analyze(code, name, df)
            if res.plan is None:
                return BatchScanItem(code=code, name=name, error="无法生成计划", extra=extra)
            plan = res.plan.to_dict()
            gate_meta = (plan.get("meta") or {}).get("quality_gate") or {}
            return BatchScanItem(
                code=code, name=name, plan=plan,
                composite_score=score_plan(plan, self.weights),
                extra=extra,
                gate_tier=str(gate_meta.get("tier") or ""),
            )
        except Exception as e:  # noqa: BLE001
            return BatchScanItem(code=code, name=name, error=str(e)[:200])
