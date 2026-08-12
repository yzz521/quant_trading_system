"""批量机会扫描器 —— 一批候选股 → 各自的交易计划。

数据流（计划书 §18 每日运行时流程）：
  候选股列表 → 逐票拉K线 → OpportunityEngine.analyze → 交易计划
并发拉取（默认 5 workers），单票失败不影响整体，输出按机会分排序、
过滤掉 AVOID / 数据不足的标的。可直接对接邮件模板 trading_plans 参数。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from ...utils import get_logger
from ..data_fetcher import detect_market, fetch_kline
from ..indicators import add_all_indicators
from .opportunity_engine import OpportunityEngine
from .trading_plan import DecisionState

log = get_logger("OpportunityBatch")

# 默认数据加载器：code → 已加指标的日K DataFrame（失败返回 None）
KlineLoader = Callable[[str, str], Optional[pd.DataFrame]]


def _default_loader(code: str, market: str = "CN") -> Optional[pd.DataFrame]:
    try:
        info = detect_market(code)
        raw = fetch_kline(info, days=250)
        if raw is None or raw.empty:
            return None
        return add_all_indicators(raw)
    except Exception as e:  # noqa: BLE001
        log.debug("拉取 %s K线失败: %s", code, e)
        return None


@dataclass
class BatchScanItem:
    """单票批量扫描结果（含原始计划，便于审计）。"""

    code: str = ""
    name: str = ""
    plan: Optional[dict] = None
    error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class BatchScanResult:
    """批量扫描结果：成功列表 + 失败列表。"""

    plans: list = field(default_factory=list)   # 过滤后的交易计划 dict（供邮件/看板）
    items: list = field(default_factory=list)   # 全部单项（含 AVOID）
    failed: list = field(default_factory=list)  # 失败项（code + error）
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "plans": self.plans,
            "items": [i.to_dict() for i in self.items],
            "failed": self.failed,
            "elapsed": round(self.elapsed, 2),
        }


class OpportunityBatchScanner:
    """批量机会扫描器。

    Args:
        engine: 机会引擎（复用账户/风控配置）；None 时用默认。
        loader: code → df 加载器；默认联网拉 250 日K并加指标。
        workers: 并发数（A股行情接口对并发敏感，默认 5）。
        min_opportunity_score: 机会分下限（过滤弱机会）。
        include_avoid: 是否保留 AVOID 到 items（plans 恒过滤 AVOID）。
    """

    def __init__(
        self,
        engine: Optional[OpportunityEngine] = None,
        loader: Optional[KlineLoader] = None,
        *,
        workers: int = 5,
        min_opportunity_score: float = 0.0,
        include_avoid: bool = True,
    ) -> None:
        self.engine = engine or OpportunityEngine()
        self.loader = loader or _default_loader
        self.workers = max(1, workers)
        self.min_opportunity_score = min_opportunity_score
        self.include_avoid = include_avoid

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

        t0 = time.time()
        items: list[BatchScanItem] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._analyze_one, c, name_map): c["code"] for c in codes}
            for fut in as_completed(futs):
                try:
                    items.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    code = futs[fut]
                    log.debug("批量分析 %s 异常: %s", code, e)
                    items.append(BatchScanItem(code=code, name=name_map.get(code, code), error=str(e)))

        items.sort(key=lambda it: (it.plan or {}).get("opportunity_score") or 0, reverse=True)

        plans = []
        for it in items:
            p = it.plan
            if not p:
                continue
            if p.get("decision") == DecisionState.AVOID.value:
                continue
            if p.get("opportunity_score") is not None and p["opportunity_score"] < self.min_opportunity_score:
                continue
            plans.append(p)

        failed = [{"code": it.code, "name": it.name, "error": it.error} for it in items if it.error]
        return BatchScanResult(
            plans=plans,
            items=items if self.include_avoid else [i for i in items if i.plan and i.plan.get("decision") != DecisionState.AVOID.value],
            failed=failed,
            elapsed=time.time() - t0,
        )

    # ------------------------------------------------------------------ #
    def _normalize(self, candidates: list, name_map: Optional[dict]) -> list[dict]:
        """把混合输入归一为 [{code, name}]。"""
        out: list[dict] = []
        for c in candidates:
            if isinstance(c, str):
                code = c
                name = (name_map or {}).get(code, code)
            elif isinstance(c, dict):
                code = str(c.get("code") or c.get("symbol") or "").strip()
                name = str(c.get("name") or (name_map or {}).get(code, code))
            else:
                continue
            if code:
                out.append({"code": code, "name": name})
        # 去重保序
        seen, dedup = set(), []
        for c in out:
            if c["code"] not in seen:
                seen.add(c["code"])
                dedup.append(c)
        return dedup

    def _analyze_one(self, cand: dict, name_map: Optional[dict]) -> BatchScanItem:
        code, name = cand["code"], cand["name"]
        try:
            df = self.loader(code, name)
            if df is None or len(df) < 60:
                return BatchScanItem(code=code, name=name, error="数据不足")
            res = self.engine.analyze(code, name, df)
            if res.plan is None:
                return BatchScanItem(code=code, name=name, error="无法生成计划")
            return BatchScanItem(code=code, name=name, plan=res.plan.to_dict())
        except Exception as e:  # noqa: BLE001
            return BatchScanItem(code=code, name=name, error=str(e)[:200])
