"""收盘漏斗选股 —— 四层过滤，把全市场压到 Top N 关注池。

数据源实测结论（当前网络环境）：
  * L1 新浪全市场快照（``stock_zh_a_spot``）—— ST/停牌/成交额
  * L2 腾讯批量行情（``qt.gtimg.cn``）—— 市值/PE/换手率
  * L3 新浪日K（复用 ``StockScanner`` 条件与评分）—— 技术面
  * L4 新浪资金流 + 可买性/持仓去重 —— 资金面与风控

东财、乐咕、深交所等接口在当前网络下不可用，故不依赖。
任一层数据失败时记录日志并跳过该层，不让单点故障中断整条漏斗。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from ..utils import get_logger
from .data_fetcher import (
    detect_market,
    fetch_kline_tencent,
    fetch_money_flow,
    fetch_stock_news,
    fetch_spot_snapshot,
    fetch_tencent_quotes,
)
from .scanner import PRESETS, ScanHit, _days_held

log = get_logger("Funnel")

DEFAULTS = {
    "top_n": 10,
    "min_amount": 50_000_000,      # L1 日成交额下限（元）
    "min_market_cap": 50,          # L2 总市值下限（亿元）
    "pe_min": 0,                   # L2 PE 区间
    "pe_max": 100,
    "min_turnover": 0.3,           # L2 换手率下限（%）
    "l3_limit": 80,                # L3 技术面后进入 L4 的候选数
    "l3_conditions": ["多头排列(新晋)", "MACD金叉", "突破新高", "放量", "超卖", "RSI健康"],
    "main_net_bonus": 10,          # L4 主力净流入为正时的加分
    "news_enabled": True,          # L4 新闻风险层开关
    "news_days": 7,                # 只看近 N 天新闻/公告
    "news_limit": 20,
    "news_penalty": 15,            # 命中风险关键词的降分
    "news_risk_keywords": [
        "诉讼", "立案", "减持", "质押", "冻结", "处罚", "违规", "问询",
        "风险警示", "退市", "终止上市", "调查", "仲裁", "合同纠纷",
        "业绩预亏", "商誉减值", "监管函", "关注函",
    ],
}


@dataclass
class FunnelStage:
    name: str
    before: int
    after: int

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class FunnelResult:
    stages: list = field(default_factory=list)
    hits: list = field(default_factory=list)
    total: int = 0
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "hits": self.hits,
            "total": self.total,
            "elapsed": self.elapsed,
        }


class FunnelScanner:
    """四层漏斗扫描器。``run()`` 一次跑完全流程并返回关注池。"""

    def __init__(self, cfg: Optional[dict] = None, max_workers: int = 8) -> None:
        merged = dict(DEFAULTS)
        if cfg:
            for k, v in cfg.items():
                if v is not None:
                    merged[k] = v
        self.cfg = merged
        self.max_workers = max_workers

    # ------------------------------------------------------------------ #
    # L1 硬过滤 —— 新浪全市场快照
    # ------------------------------------------------------------------ #
    def stage_l1(self, spot: Optional[pd.DataFrame]) -> list[str]:
        """剔 ST/*ST/退市/停牌（名称含 ST 或 退、现价≤0、成交额=0），成交额≥min_amount。"""
        if spot is None or spot.empty:
            return []
        df = spot.copy()
        df = df[~df["name"].astype(str).str.contains("ST|退", na=False)]
        df = df[pd.to_numeric(df["close"], errors="coerce").fillna(0) > 0]
        df = df[pd.to_numeric(df["amount"], errors="coerce").fillna(0) >= self.cfg["min_amount"]]
        return df["code"].astype(str).tolist()

    # ------------------------------------------------------------------ #
    # L2 质量过滤 —— 腾讯批量行情
    # ------------------------------------------------------------------ #
    def stage_l2(self, quotes: Optional[pd.DataFrame]) -> pd.DataFrame:
        """市值≥min_market_cap（亿）、pe_min<PE<pe_max、换手率≥min_turnover。"""
        if quotes is None or quotes.empty:
            return quotes
        df = quotes.copy()
        df = df[pd.to_numeric(df["total_cap_yi"], errors="coerce").fillna(0) >= self.cfg["min_market_cap"]]
        pe = pd.to_numeric(df["pe"], errors="coerce")
        df = df[pe.notna() & (pe > self.cfg["pe_min"]) & (pe < self.cfg["pe_max"])]
        df = df[pd.to_numeric(df["turnover"], errors="coerce").fillna(0) >= self.cfg["min_turnover"]]
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # L3 技术面 —— 复用 StockScanner 条件与评分（多线程）
    # ------------------------------------------------------------------ #
    def _make_tencent_evaluator(self, name_map: dict) -> Callable:
        """基于腾讯日K的单票评估器（纯 urllib，线程安全）。

        akshare 的新浪日K接口底层含非线程安全的 JS 引擎，多线程会崩溃，
        故漏斗 L3 不用 ``StockScanner._evaluate``，改走腾讯K线。
        """
        from .indicators import add_all_indicators
        from .patterns import scan_signals

        def evaluate(code: str, resolved: list) -> Optional[ScanHit]:
            try:
                info = detect_market(code)
                df = fetch_kline_tencent(info, days=120)
                if df.empty or len(df) < 30:
                    return None
                df = add_all_indicators(df)
                ind = df.iloc[-1]
                matched: list[str] = []
                matched_days: dict[str, int] = {}
                for name, cond in resolved:
                    if cond(df, ind):
                        matched.append(name)
                        matched_days[name] = _days_held(df, cond)
                if not matched:
                    return None
                c = float(ind["close"])
                chg = float(
                    (df["close"].iloc[-1] - df["close"].iloc[-2])
                    / df["close"].iloc[-2] * 100
                ) if len(df) >= 2 else 0.0
                signals = scan_signals(df)
                score = len(matched) * 15 + sum(5 for s in signals if s.get("type") == "bull")
                return ScanHit(
                    code=info.code,
                    name=name_map.get(str(info.code).zfill(6), info.code),
                    market=info.market,
                    close=round(c, 4),
                    change_pct=round(chg, 2),
                    score=min(score, 100),
                    matched=matched,
                    matched_days=matched_days,
                    signals=signals,
                )
            except Exception as e:  # noqa: BLE001
                log.debug("漏斗 L3 单票失败 %s: %s", code, e)
                return None

        return evaluate

    def _technical_pass(
        self,
        codes: list[str],
        evaluator: Optional[Callable] = None,
        name_map: Optional[dict] = None,
    ) -> list[ScanHit]:
        resolved = [(n, PRESETS[n]) for n in self.cfg["l3_conditions"] if n in PRESETS]
        if not resolved:
            log.warning("漏斗 L3 无有效条件")
            return []
        if evaluator is None:
            evaluator = self._make_tencent_evaluator(name_map or {})

        hits: list[ScanHit] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = [ex.submit(evaluator, c, resolved) for c in codes]
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.debug("漏斗 L3 单票失败: %s", e)
                    continue
                if r is not None:
                    hits.append(r)
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: self.cfg["l3_limit"]]

    # ------------------------------------------------------------------ #
    # L4 资金面 + 风控 —— 主力资金加分、持仓去重、可买性标注、Top N
    # ------------------------------------------------------------------ #
    def stage_l4(
        self,
        hits: list[ScanHit],
        quote_map: dict[str, dict],
        holdings_mgr=None,
        held_codes: Optional[set] = None,
        news_fetcher: Optional[Callable] = None,
    ) -> list[dict]:
        held = {str(c).strip() for c in (held_codes or set())}
        items: list[dict] = []
        for h in hits:
            d = h.to_dict()
            q = quote_map.get(str(h.code).zfill(6), {})
            mf = fetch_money_flow(h.code)
            d.update(
                market_cap=q.get("total_cap_yi"),
                pe=q.get("pe"),
                turnover=q.get("turnover"),
                main_net=(mf or {}).get("main_net"),
                buy_tag="",
                buy_label="",
            )
            if d.get("main_net") is not None and d["main_net"] > 0:
                d["score"] = min(100, d["score"] + self.cfg["main_net_bonus"])
            items.append(d)

        # 持仓去重：已持有的不再进入关注池（自选股诊断里另有覆盖）
        items = [it for it in items if str(it["code"]).strip() not in held]

        # 新闻风险层：命中风险关键词的降分并标注（多线程拉取）
        items = self._news_risk_pass(items, news_fetcher=news_fetcher)

        # 可买性标注（未配置总资金时 annotate_list 原样返回）
        if holdings_mgr is not None:
            try:
                from .buy_power import annotate_list
                _, items = annotate_list(
                    items,
                    holdings_mgr=holdings_mgr,
                    price_key="close",
                    default_market="CN",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("漏斗 L4 可买性标注失败: %s", e)

        items.sort(key=lambda it: it.get("score") or 0, reverse=True)
        return items[: self.cfg["top_n"]]

    def _news_risk_pass(
        self,
        items: list[dict],
        news_fetcher: Optional[Callable] = None,
    ) -> list[dict]:
        """近 N 天新闻/公告标题命中风险关键词 → 降分 + 标注 news_risks。"""
        if not items:
            return items
        keywords = self.cfg.get("news_risk_keywords") or []
        if not self.cfg.get("news_enabled", True) or not keywords:
            return items
        fetcher = news_fetcher or fetch_stock_news
        days = int(self.cfg.get("news_days", 7))
        limit = int(self.cfg.get("news_limit", 20))
        penalty = int(self.cfg.get("news_penalty", 15))

        results: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {
                ex.submit(fetcher, it["code"], days=days, limit=limit): it["code"]
                for it in items
            }
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    results[code] = fut.result() or []
                except Exception:  # noqa: BLE001
                    results[code] = []

        for it in items:
            hits = []
            for n in results.get(str(it["code"]).strip(), []):
                matched = [k for k in keywords if k in n.get("title", "")]
                if matched:
                    hits.append({"title": n["title"], "url": n["url"], "keywords": matched})
                    if len(hits) >= 3:
                        break
            it["news_risks"] = hits
            if hits:
                it["score"] = max(0, (it.get("score") or 0) - penalty)
                it["risk_flag"] = True
        return items

    # ------------------------------------------------------------------ #
    def run(self, holdings_mgr=None) -> dict:
        t0 = time.time()
        stages: list[FunnelStage] = []
        total = 0

        spot = fetch_spot_snapshot()
        total = 0 if spot is None else len(spot)
        l1 = self.stage_l1(spot)
        stages.append(FunnelStage("L1 硬过滤", total, len(l1)))
        if not l1:
            log.warning("漏斗 L1 无候选，终止")
            return FunnelResult(stages=stages, total=total).to_dict()
        if self.cfg.get("l1_limit"):
            # 抽样取有代表性的候选（固定种子），避免只取到列表头部的同类股票
            import random
            l1 = random.Random(42).sample(l1, min(int(self.cfg["l1_limit"]), len(l1)))

        quotes = fetch_tencent_quotes(l1)
        qdf = self.stage_l2(quotes)
        l2_codes: list[str] = [] if qdf is None else qdf["code"].astype(str).tolist()
        stages.append(FunnelStage("L2 质量过滤", len(l1), len(l2_codes)))
        if not l2_codes:
            log.warning("漏斗 L2 无候选，终止")
            return FunnelResult(stages=stages, total=total).to_dict()
        quote_map = {
            str(r["code"]).zfill(6): r.to_dict()
            for _, r in qdf.iterrows()
        }

        name_map = {
            str(r["code"]).zfill(6): str(r.get("name") or "")
            for _, r in qdf.iterrows()
        }
        tech = self._technical_pass(l2_codes, name_map=name_map)
        stages.append(FunnelStage("L3 技术面", len(l2_codes), len(tech)))
        if not tech:
            log.warning("漏斗 L3 无命中，终止")
            return FunnelResult(stages=stages, total=total).to_dict()

        held: set = set()
        if holdings_mgr is not None:
            try:
                held = {str(r.get("code") or "").strip() for r in holdings_mgr.all()}
            except Exception as e:  # noqa: BLE001
                log.debug("读取持仓失败: %s", e)

        top = self.stage_l4(tech, quote_map, holdings_mgr, held)
        stages.append(FunnelStage("L4 资金+风控", len(tech), len(top)))
        log.info(
            "漏斗完成: %s → %s 只，耗时 %.1fs",
            " → ".join(f"{s.before}>{s.after}" for s in stages),
            len(top),
            time.time() - t0,
        )
        return FunnelResult(
            stages=stages,
            hits=top,
            total=total,
            elapsed=round(time.time() - t0, 1),
        ).to_dict()
