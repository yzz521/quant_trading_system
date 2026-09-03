"""机会引擎（OpportunityEngine）—— 把一次个股分析串成完整交易计划。

流程（计划书 §04/§18）：
  日K → 支撑/阻力 → 入场区间 → 止损/目标 → 风险收益 → 评分(个股/机会) → 仓位 → TradingPlan
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..indicators import rate_signals
from ..patterns import pattern_score, recent_pattern_names
from ..scoring.opportunity_score import OpportunityScore, calc_opportunity_score
from ..scoring.stock_score import StockScore, calc_stock_score
from .entry_price import EntryPrice, calc_entry_zone
from .exit_price import ExitPrice, calc_exit_prices
from .position_sizing import PositionSizing, calc_position_size
from .risk_reward import RiskReward, calc_risk_reward
from .support_resistance import SupportResistance, detect_support_resistance
from .trading_plan import TradingPlan, build_trading_plan


@dataclass
class OpportunityResult:
    """单票完整分析结果（供 AI 与 Dashboard 消费）。"""

    code: str = ""
    name: str = ""
    plan: Optional[TradingPlan] = None
    sr: Optional[SupportResistance] = None
    entry: Optional[EntryPrice] = None
    exit_: Optional[ExitPrice] = None
    rr: Optional[RiskReward] = None
    stock_score: Optional[StockScore] = None
    opportunity_score: Optional[OpportunityScore] = None
    position: Optional[PositionSizing] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "plan": self.plan.to_dict() if self.plan else None,
            "support_resistance": self.sr.to_dict() if self.sr else None,
            "entry": self.entry.to_dict() if self.entry else None,
            "exit": self.exit_.to_dict() if self.exit_ else None,
            "risk_reward": self.rr.to_dict() if self.rr else None,
            "stock_score": self.stock_score.to_dict() if self.stock_score else None,
            "opportunity_score": self.opportunity_score.to_dict() if self.opportunity_score else None,
            "position": self.position.to_dict() if self.position else None,
        }


class OpportunityEngine:
    """单票机会分析引擎。"""

    def __init__(
        self,
        *,
        account_equity: Optional[float] = None,
        risk_percent: float = 0.02,
        max_position_pct: float = 0.20,
        market_factor: float = 1.0,
        regime_score: Optional[float] = None,
        sector_map: Optional[dict] = None,
        sector_rank: Optional[list] = None,
        fetch_news: bool = False,
    ) -> None:
        self.account_equity = account_equity
        self.risk_percent = risk_percent
        self.max_position_pct = max_position_pct
        self.market_factor = market_factor
        self.regime_score = regime_score
        # Sector Rotation：{code6: 板块名} + 板块强度排名（可为 None，失败中性 50）
        self.sector_map = sector_map or {}
        self.sector_rank = sector_rank or []
        # 仅实时扫描开启。回测必须保持 False，否则会把「今天的公告」套到历史K线上。
        self.fetch_news = fetch_news

    def analyze(
        self,
        code: str,
        name: str = "",
        df: Optional[pd.DataFrame] = None,
        *,
        extra: Optional[dict] = None,
        news_risks: Optional[list] = None,
        news_catalysts: Optional[list] = None,
        similar_pattern_score: Optional[float] = None,
    ) -> OpportunityResult:
        """对单票执行完整机会分析。

        Args:
            code: 股票代码。
            name: 股票名称。
            df: 已加指标的日K（至少 60 根）。
            extra: 外部数据（市值/PE/主力净流入等，供 Stock Score）。
            news_risks: 新闻风险条目；None 且 fetch_news=True 时自动拉取。
            news_catalysts: 利好条目；与 news_risks 一同由信息面生成。
            similar_pattern_score: 历史相似形态分。
        """
        extra = extra or {}
        if df is None or len(df) < 30:
            return OpportunityResult(code=code, name=name)

        info_snap = None
        if news_risks is None and self.fetch_news:
            try:
                from ..news import fetch_and_rate

                info_snap = fetch_and_rate(code, name)
                news_risks = info_snap.risks
                news_catalysts = info_snap.catalysts
            except Exception:  # noqa: BLE001
                news_risks, news_catalysts = [], []
        news_risks = news_risks or []
        news_catalysts = news_catalysts or []

        if similar_pattern_score is None:
            similar_pattern_score = pattern_score(df)
        tech = rate_signals(df)
        pattern_names = recent_pattern_names(df)

        # 1) 支撑/阻力
        sr = detect_support_resistance(df)

        # 2) 入场区间
        entry = calc_entry_zone(df, sr=sr)

        # 3) 止损 + 目标价
        cur = float(df["close"].iloc[-1])
        exit_ = calc_exit_prices(df, entry_price=entry.standard or cur, sr=sr)

        # 4) 风险收益比
        rr = calc_risk_reward(
            entry.standard or cur,
            exit_.stop_loss or 0,
            exit_.target_1 or 0,
            exit_.target_2 or 0,
        )

        # 5) 双评分（含 Sector Rotation：板块强度因子）
        stock_sector = (self.sector_map or {}).get(code)
        sector_score = None
        if stock_sector:
            from ..sector import sector_factor

            sector_score = sector_factor(stock_sector, self.sector_rank)
        stock_score = calc_stock_score(
            df, extra=extra, regime_score=self.regime_score,
            sector_score=sector_score, news_risks=news_risks,
            news_catalysts=news_catalysts,
        )
        opportunity_score = calc_opportunity_score(
            df,
            current_price=cur,
            entry_low=entry.low,
            entry_high=entry.high,
            key_support=sr.key_support,
            risk_reward_1=rr.ratio_1,
            similar_pattern_score=similar_pattern_score,
        )

        # 6) 仓位（AVOID 决策不计算仓位）
        position = None
        position_percent = None
        avoid = rr.ratio_1 is not None and rr.ratio_1 < 1.5
        if self.account_equity and not avoid:
            position = calc_position_size(
                self.account_equity,
                entry.standard or cur,
                exit_.stop_loss or 0,
                risk_percent=self.risk_percent,
                max_position_pct=self.max_position_pct,
                market_factor=self.market_factor,
            )
            position_percent = position.position_percent

        # 7) 置信度：由机会分与 RR 融合
        confidence = 0.0
        if opportunity_score.total and rr.ratio_1:
            confidence = min(0.98, (opportunity_score.total / 100) * 0.6 + min(rr.ratio_1, 4.0) / 4.0 * 0.4)
        if info_snap and info_snap.severe:
            confidence = min(confidence, confidence * 0.75)
        confidence = round(confidence, 2)

        # 8) 理由/风险/失效条件
        reasons = self._build_reasons(
            sr, entry, exit_, rr, opportunity_score, tech, pattern_names, info_snap,
        )
        risks = self._build_risks(df, sr, exit_, news_risks, pattern_names)
        invalidate = (
            f"收盘跌破 {exit_.stop_loss}（止损位）即视为逻辑失效"
            if exit_.stop_loss else ""
        )

        plan = build_trading_plan(
            code=code,
            name=name or code,
            current_price=cur,
            entry=entry,
            exit_=exit_,
            rr=rr,
            stock_score=stock_score.total,
            opportunity_score=opportunity_score.total,
            position_percent=position_percent,
            confidence=confidence,
            reasons=reasons,
            risks=risks,
            invalidate_condition=invalidate,
        )
        # 板块信息写入 meta（供 Dashboard/邮件展示）
        if stock_sector:
            plan.meta["sector"] = stock_sector
            plan.meta["sector_score"] = round(sector_score or 50.0, 1)
        plan.meta["technical"] = tech
        if pattern_names:
            plan.meta["patterns"] = pattern_names
        if sr and sr.evidence.get("fibonacci"):
            plan.meta["fibonacci"] = sr.evidence["fibonacci"]
        if info_snap is not None:
            plan.meta["information"] = info_snap.to_dict()

        return OpportunityResult(
            code=code,
            name=name or code,
            plan=plan,
            sr=sr,
            entry=entry,
            exit_=exit_,
            rr=rr,
            stock_score=stock_score,
            opportunity_score=opportunity_score,
            position=position,
        )

    # ------------------------------------------------------------------ #
    def _build_reasons(
        self,
        sr: SupportResistance,
        entry: EntryPrice,
        exit_: ExitPrice,
        rr: RiskReward,
        opp: OpportunityScore,
        tech: Optional[dict] = None,
        pattern_names: Optional[list[str]] = None,
        info_snap=None,
    ) -> list[str]:
        reasons: list[str] = []
        if tech and tech.get("grade"):
            tags = "，".join((tech.get("tags") or [])[:3])
            extra = f"：{tags}" if tags else ""
            reasons.append(f"技术面 {tech['grade']} 级{extra}")
        if info_snap is not None and (info_snap.tags or info_snap.grade != "中性"):
            tags = "，".join((info_snap.tags or [])[:3])
            extra = f"：{tags}" if tags else ""
            reasons.append(f"信息面{info_snap.grade}{extra}")
        if pattern_names:
            reasons.append("K线形态：" + "、".join(pattern_names[:3]))
        if sr and sr.key_support is not None:
            reasons.append(f"关键支撑位于 {sr.key_support}，为下方安全边际")
        if entry and entry.standard is not None:
            reasons.append(f"标准入场 {entry.standard}，入场区间 {entry.low}~{entry.high}")
        if exit_ and exit_.target_1 is not None:
            reasons.append(f"第一目标 {exit_.target_1}（预期收益 {exit_.expected_return}%）")
        if rr and rr.ratio_1 is not None:
            reasons.append(f"风险收益比 1:{rr.ratio_1}（{rr.grade}）")
        if opp and opp.total:
            reasons.append(f"机会评分 {round(opp.total, 1)}/100")
        return reasons[:6]

    def _build_risks(
        self,
        df: pd.DataFrame,
        sr: SupportResistance,
        exit_: ExitPrice,
        news_risks: Optional[list] = None,
        pattern_names: Optional[list[str]] = None,
    ) -> list[str]:
        risks: list[str] = []
        if exit_ and exit_.stop_loss is not None:
            risks.append(f"若跌破止损 {exit_.stop_loss} 需离场")
        if df is not None and len(df) >= 20:
            last = df.iloc[-1]
            rsi = pd.to_numeric(df["rsi6"], errors="coerce").iloc[-1] if "rsi6" in df.columns else None
            if rsi is not None and not pd.isna(rsi) and rsi > 75:
                risks.append(f"RSI6={rsi:.0f} 短期超买，警惕回踩")
            j = last["j"] if "j" in df.columns else None
            try:
                jv = float(j) if j is not None else None
            except (TypeError, ValueError):
                jv = None
            if jv is not None and not pd.isna(jv) and jv > 100:
                risks.append(f"KDJ.J={jv:.0f} 超买")
            width = last["boll_width"] if "boll_width" in df.columns else None
            try:
                wv = float(width) if width is not None else None
            except (TypeError, ValueError):
                wv = None
            if wv is not None and not pd.isna(wv) and 0 < wv < 0.04:
                risks.append("布林带宽挤压，变盘临近")
        bearish = [n for n in (pattern_names or []) if n in ("射击之星", "看跌吞没", "暮星", "三黑鸦", "大阴线")]
        if bearish:
            risks.append("出现" + "、".join(bearish) + "，注意见顶回撤")
        if news_risks:
            titles = []
            for it in news_risks[:2]:
                t = it.get("title") if isinstance(it, dict) else str(it)
                if t:
                    titles.append(str(t)[:36])
            if titles:
                risks.append("公告/新闻风险：" + "；".join(titles))
            else:
                risks.append(f"近期新闻命中 {len(news_risks)} 条风险关键词")
        return risks[:4]
