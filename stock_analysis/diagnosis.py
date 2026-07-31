"""Stock diagnosis engine — turns market data into a structured verdict.

Combines four dimensions:

1. **Technical signals** (patterns.py) — MA/MACD/RSI/KDJ/Bollinger/breakout
2. **Trend structure** — MA alignment, price vs MA20/MA60
3. **Momentum** — MACD histogram, RSI zone, KDJ state
4. **Fund flow & valuation** — A-share only, best-effort (degrades gracefully)

Each dimension contributes to a 0-100 score which maps to a rating
(强烈买入 / 买入 / 观望 / 减持 / 卖出).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..utils import get_logger
from .data_fetcher import MarketInfo, detect_market, fetch_kline, fetch_name, fetch_fund_flow, fetch_valuation
from .indicators import add_all_indicators
from .patterns import scan_signals

log = get_logger("Diagnoser")

RATING_BANDS = [
    (75, "强烈买入", "bull"),
    (60, "买入", "bull"),
    (40, "观望", "neutral"),
    (25, "减持", "bear"),
    (0, "卖出", "bear"),
]


@dataclass
class DiagnosisResult:
    code: str
    name: str
    market: str
    price: float
    change_pct: float
    indicators: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)
    trend: str = ""
    fund_flow: Optional[dict] = None
    valuation: Optional[dict] = None
    score: int = 50
    rating: str = "观望"
    risks: list = field(default_factory=list)
    summary: str = ""
    advice: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "market": self.market,
            "price": self.price, "change_pct": self.change_pct,
            "score": self.score, "rating": self.rating, "trend": self.trend,
            "summary": self.summary,
            "indicators": self.indicators, "signals": self.signals,
            "fund_flow": self.fund_flow, "valuation": self.valuation,
            "risks": self.risks, "advice": self.advice,
        }


class StockDiagnoser:
    def diagnose(self, code: str, days: int = 250) -> DiagnosisResult:
        info = detect_market(code)
        log.info("Diagnosing %s [%s] ...", info.code, info.market)
        df = fetch_kline(info, days=days)
        if df.empty or len(df) < 30:
            raise ValueError(f"数据不足，无法分析 {code}（仅 {len(df)} 条）")

        name = fetch_name(info)
        df = add_all_indicators(df)
        signals = scan_signals(df)
        last = df.iloc[-1]

        # --- dimension scores ---
        tech_score, trend_score, mom_score = self._score_technicals(df, signals)
        ff_score, fund_flow = self._score_fund_flow(info)
        val_score, valuation = self._score_valuation(info, last)

        total = int(np.clip(tech_score + trend_score + mom_score + ff_score + val_score, 0, 100))
        rating = self._rating(total)

        price = float(last["close"])
        change_pct = float((last["close"] - df.iloc[-2]["close"]) / df.iloc[-2]["close"] * 100) if len(df) >= 2 else 0.0
        trend = self._trend_label(df)
        risks = self._risks(df, signals, fund_flow)
        summary = self._summary(info, name, rating, total, trend, signals)

        ind_snapshot = self._indicator_snapshot(last)
        advice = self._compute_advice(last, rating)
        return DiagnosisResult(
            code=info.code, name=name, market=info.market,
            price=round(price, 4), change_pct=round(change_pct, 2),
            indicators=ind_snapshot, signals=signals, trend=trend,
            fund_flow=fund_flow, valuation=valuation,
            score=total, rating=rating, risks=risks, summary=summary,
            advice=advice,
        )

    # ------------------------------------------------------------------ #
    def _compute_advice(self, last: pd.Series, rating: str) -> dict:
        """ATR-based buy / stop-loss / take-profit levels.

        stop = close - 2*ATR, take = close + 3*ATR  (reward:risk = 1.5:1).
        For bearish ratings only the exit level is given.
        """
        close = last.get("close")
        atr = last.get("atr")
        if close is None or atr is None:
            return {}
        try:
            close, atr = float(close), float(atr)
        except (TypeError, ValueError):
            return {}
        if np.isnan(atr) or atr <= 0 or np.isnan(close):
            return {}
        if rating in ("强烈买入", "买入", "观望"):
            action = "可买入" if rating != "观望" else "观望待确认"
            return {
                "action": action,
                "buy_price": round(close, 2),
                "stop_loss": round(close - 2 * atr, 2),
                "take_profit": round(close + 3 * atr, 2),
                "risk_reward": "1:1.5",
                "atr": round(atr, 3),
            }
        return {
            "action": "不建议买入",
            "buy_price": None,
            "stop_loss": round(close, 2),
            "take_profit": None,
            "risk_reward": "—",
            "atr": round(atr, 3),
        }

    # ------------------------------------------------------------------ #
    def _score_technicals(self, df: pd.DataFrame, signals: list):
        # 1) signal-based technical score (0-40)
        bull = sum(1 for s in signals if s.get("type") == "bull")
        bear = sum(1 for s in signals if s.get("type") == "bear")
        tech = np.clip((bull - bear) * 8 + 20, 0, 40)

        # 2) trend structure (0-20)
        last = df.iloc[-1]
        c, ma5, ma20, ma60 = last["close"], last["ma5"], last["ma20"], last["ma60"]
        t = 10
        if all(not np.isnan(x) for x in (ma5, ma20, ma60)):
            if ma5 > ma20 > ma60:
                t += 8
            elif ma5 < ma20 < ma60:
                t -= 8
            if not np.isnan(c):
                if c > ma20:
                    t += 2
                else:
                    t -= 2
                if c > ma60:
                    t += 2
                else:
                    t -= 2
        trend = float(np.clip(t, 0, 20))

        # 3) momentum (0-20)
        m = 10
        if not np.isnan(last["macd_hist"]):
            m += 4 if last["macd_hist"] > 0 else -4
        rsi = last["rsi12"]
        if not np.isnan(rsi):
            if 40 <= rsi <= 60:
                m += 4
            elif rsi < 30:
                m += 2   # oversold bounce potential
            elif rsi > 70:
                m -= 4
        j = last["j"]
        if not np.isnan(j):
            if j < 20:
                m += 2
            elif j > 90:
                m -= 2
        mom = float(np.clip(m, 0, 20))
        return tech, trend, mom

    def _score_fund_flow(self, info: MarketInfo):
        if info.market != "CN":
            return 5.0, None  # neutral for US/HK
        ff = fetch_fund_flow(info)
        if ff is None or ff.empty:
            return 5.0, None
        # Try common column names for main-force net inflow
        col = None
        for c in ff.columns:
            if "主力" in c and "净额" in c:
                col = c
                break
        if col is None:
            return 5.0, None
        recent = ff[col].astype(float).tail(5)
        net = float(recent.sum())
        score = 10.0 if net > 0 else 0.0
        return score, {
            "net_5d": round(net, 0),
            "latest": float(recent.iloc[-1]),
            "direction": "净流入" if net > 0 else "净流出",
        }

    def _score_valuation(self, info: MarketInfo, last: pd.Series):
        if info.market != "CN":
            return 5.0, None
        val = fetch_valuation(info)
        if val is None or val.empty:
            return 5.0, None
        row = val.iloc[-1]
        pe = self._pick(row, ["pe_ttm", "total_pe", "pe"])
        pb = self._pick(row, ["pb", "total_pb"])
        # Heuristic: low PE/PB → higher score
        score = 5.0
        if pe is not None and not np.isnan(pe):
            if pe < 15:
                score += 4
            elif pe < 30:
                score += 2
            elif pe > 60:
                score -= 2
        if pb is not None and not np.isnan(pb) and pb < 1.5:
            score += 1
        return float(np.clip(score, 0, 10)), {
            "pe_ttm": None if pe is None else round(float(pe), 2),
            "pb": None if pb is None else round(float(pb), 2),
        }

    @staticmethod
    def _pick(row, names):
        for n in names:
            if n in row.index:
                v = row[n]
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _rating(score: int) -> str:
        for threshold, label, _ in RATING_BANDS:
            if score >= threshold:
                return label
        return "卖出"

    @staticmethod
    def _trend_label(df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        c, ma5, ma20, ma60 = last["close"], last["ma5"], last["ma20"], last["ma60"]
        if any(np.isnan(x) for x in (ma5, ma20, ma60)):
            return "数据不足"
        if ma5 > ma20 > ma60 and c > ma20:
            return "上升趋势"
        if ma5 < ma20 < ma60 and c < ma20:
            return "下降趋势"
        if c > ma20:
            return "震荡偏多"
        return "震荡偏空"

    @staticmethod
    def _indicator_snapshot(last: pd.Series) -> dict:
        def f(v):
            return None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), 3)
        return {
            "MA5": f(last["ma5"]), "MA10": f(last["ma10"]),
            "MA20": f(last["ma20"]), "MA60": f(last["ma60"]),
            "MACD": f(last["macd_dif"]), "MACD_Signal": f(last["macd_dea"]),
            "MACD_Hist": f(last["macd_hist"]),
            "RSI6": f(last["rsi6"]), "RSI12": f(last["rsi12"]),
            "K": f(last["k"]), "D": f(last["d"]), "J": f(last["j"]),
            "BOLL_Upper": f(last["boll_upper"]), "BOLL_Lower": f(last["boll_lower"]),
            "ATR": f(last["atr"]), "CCI": f(last["cci"]),
            "Volume_Ratio": f(last["vr"]),
        }

    @staticmethod
    def _risks(df: pd.DataFrame, signals: list, fund_flow) -> list:
        risks = []
        last = df.iloc[-1]
        if not np.isnan(last["rsi12"]) and last["rsi12"] > 75:
            risks.append("RSI 超买，短期回调风险")
        if not np.isnan(last["j"]) and last["j"] > 100:
            risks.append("KDJ 超买，高位钝化风险")
        if not np.isnan(last["macd_hist"]) and last["macd_hist"] < 0:
            risks.append("MACD 绿柱，动能偏弱")
        bear = [s for s in signals if s.get("type") == "bear"]
        if len(bear) >= 2:
            risks.append(f"出现 {len(bear)} 个看空信号：" + "、".join(s["name"] for s in bear))
        if fund_flow and fund_flow.get("net_5d", 0) < 0:
            risks.append("近5日主力资金净流出")
        # volatility
        vol = df["close"].pct_change().tail(20).std() * np.sqrt(252)
        if not np.isnan(vol) and vol > 0.5:
            risks.append(f"年化波动率 {vol*100:.0f}%，波动较大")
        if not risks:
            risks.append("暂未触发明显风险信号")
        return risks

    @staticmethod
    def _summary(info, name, rating, score, trend, signals) -> str:
        bull = [s for s in signals if s.get("type") == "bull"]
        bear = [s for s in signals if s.get("type") == "bear"]
        parts = [f"{name}({info.code}) 综合评分 {score}/100，评级【{rating}】，趋势：{trend}。"]
        if bull:
            parts.append("看多信号：" + "、".join(s["name"] for s in bull) + "。")
        if bear:
            parts.append("看空信号：" + "、".join(s["name"] for s in bear) + "。")
        if not bull and not bear:
            parts.append("近期无明显趋势信号，建议观望。")
        return "".join(parts)
