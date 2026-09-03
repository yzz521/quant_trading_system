"""生成三市场(A股/美股/港股)邮件预览，验证 main-v3 模板（持仓/资金/今日机会/卖出参考）.

Run: python examples/gen_email_preview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import build_market_message


# ---------- 三市场模拟数据 ----------
def _holdings(market: str) -> tuple[list, dict]:
    if market == "CN":
        holdings = [
            {"code": "600000", "name": "浦发银行", "market": "CN", "cost_price": 8.50, "quantity": 2000,
             "current_price": 9.28, "market_value": 18560.0, "pnl": 1560.0, "pnl_pct": 9.18, "hold_days": 76, "buy_date": "2024-05-15"},
            {"code": "600519", "name": "贵州茅台", "market": "CN", "cost_price": 1750.00, "quantity": 100,
             "current_price": 1680.50, "market_value": 168050.0, "pnl": -6950.0, "pnl_pct": -3.97, "hold_days": 101, "buy_date": "2024-04-20"},
        ]
        summary = {"total_cost": 192000.0, "total_value": 186610.0, "total_pnl": -5390.0, "total_pnl_pct": -2.81, "count": 2}
    elif market == "US":
        holdings = [
            {"code": "AAPL", "name": "Apple", "market": "US", "cost_price": 185.00, "quantity": 50,
             "current_price": 338.19, "market_value": 16909.50, "pnl": 7659.50, "pnl_pct": 82.81, "hold_days": 59, "buy_date": "2024-06-01"},
        ]
        summary = {"total_cost": 9250.0, "total_value": 16909.50, "total_pnl": 7659.50, "total_pnl_pct": 82.81, "count": 1}
    else:  # HK
        holdings = [
            {"code": "00700", "name": "腾讯控股", "market": "HK", "cost_price": 320.00, "quantity": 200,
             "current_price": 385.60, "market_value": 77120.0, "pnl": 13120.0, "pnl_pct": 20.5, "hold_days": 45, "buy_date": "2024-06-28"},
        ]
        summary = {"total_cost": 64000.0, "total_value": 77120.0, "total_pnl": 13120.0, "total_pnl_pct": 20.5, "count": 1}
    return holdings, summary


def _actions(market: str) -> list:
    if market == "CN":
        return [
            {"code": "600000", "name": "浦发银行", "action": "持有", "note": "趋势健康，继续持有", "pnl_pct": 9.18},
            {"code": "600519", "name": "贵州茅台", "action": "加仓", "note": "回调至支撑区间", "pnl_pct": -3.97},
        ]
    if market == "US":
        return [{"code": "AAPL", "name": "Apple", "action": "持有", "note": "上行趋势未破", "pnl_pct": 82.81}]
    return [{"code": "00700", "name": "腾讯控股", "action": "持有", "note": "量价健康", "pnl_pct": 20.5}]


def _quant(market: str) -> list:
    if market == "CN":
        return [
            {"code": "600000", "name": "浦发银行", "action": "HOLD", "action_label": "持有",
             "action_emoji": "🟡", "current_price": 9.28, "pnl_pct": 9.18, "stock_score": 71,
             "opportunity_score": 66, "tech_grade": "A", "info_grade": "中性",
             "stop_loss": 8.70, "note": "持有观察"},
            {"code": "600519", "name": "贵州茅台", "action": "ADD", "action_label": "可加仓",
             "action_emoji": "🟢", "current_price": 1680.50, "pnl_pct": -3.97, "stock_score": 78,
             "opportunity_score": 70, "tech_grade": "A", "info_grade": "中性",
             "stop_loss": 1580.0, "note": "趋势仍在且回踩入场区，可观察加仓"},
        ]
    if market == "US":
        return [{"code": "AAPL", "name": "Apple", "action": "HOLD", "action_label": "持有",
                 "action_emoji": "🟡", "current_price": 338.19, "pnl_pct": 82.81, "stock_score": 80,
                 "opportunity_score": 62, "tech_grade": "S", "info_grade": "中性",
                 "stop_loss": 290.0, "note": "已有浮盈：持有、不加仓追高"}]
    return [{"code": "00700", "name": "腾讯控股", "action": "HOLD", "action_label": "持有",
             "action_emoji": "🟡", "current_price": 385.60, "pnl_pct": 20.5, "stock_score": 74,
             "opportunity_score": 68, "tech_grade": "A", "info_grade": "中性",
             "stop_loss": 350.0, "note": "持有观察"}]


def _plans(market: str) -> list:
    base = {
        "stock_score": 72.0, "opportunity_score": 78.0, "current_price": 12.36,
        "entry_low": 11.80, "entry_price": 11.95, "entry_high": 12.10,
        "stop_loss": 11.35, "target_1": 13.20, "target_2": 14.50,
        "risk_reward_1": 2.08, "position_percent": 20.0,
    }
    if market == "CN":
        return [
            {**base, "code": "600036", "name": "招商银行", "decision": "BUY_NOW"},
            {**base, "code": "601318", "name": "中国平安", "decision": "BUY_ON_PULLBACK", "current_price": 48.62, "entry_low": 47.10, "entry_high": 48.30, "stop_loss": 45.80, "target_1": 51.00, "target_2": 55.00, "risk_reward_1": 3.1},
        ]
    if market == "US":
        return [{**base, "code": "MSFT", "name": "Microsoft", "decision": "WATCH", "current_price": 445.20}]
    return [{**base, "code": "09988", "name": "阿里巴巴", "decision": "BUY_ON_PULLBACK", "current_price": 82.30}]


for market in ("CN", "US", "HK"):
    holdings, summary = _holdings(market)
    title, text, html = build_market_message(
        market,
        holdings=holdings, holdings_summary=summary,
        holding_actions=_actions(market),
        holding_quant=_quant(market),
        trading_plans=_plans(market),
    )
    out = f"results/email_{market.lower()}_preview.html"
    Path(out).write_text(html, encoding="utf-8")
    print(f"✓ {market}: {out}")
    print(f"  标题: {title}")
    print(f"  纯文本预览:\n{text[:300]}\n")
