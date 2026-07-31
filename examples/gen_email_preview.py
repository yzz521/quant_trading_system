"""生成三市场(A股/美股/港股)邮件预览，验证美化模板+持仓段+多市场.

Run: python examples/gen_email_preview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import build_market_message

# ---------- A股 ----------
cn_holdings = [
    {"code": "600000", "name": "浦发银行", "market": "CN", "cost_price": 8.50, "quantity": 2000,
     "current_price": 9.28, "market_value": 18560.0, "pnl": 1560.0, "pnl_pct": 9.18, "hold_days": 76, "buy_date": "2024-05-15"},
    {"code": "600519", "name": "贵州茅台", "market": "CN", "cost_price": 1750.00, "quantity": 100,
     "current_price": 1680.50, "market_value": 168050.0, "pnl": -6950.0, "pnl_pct": -3.97, "hold_days": 101, "buy_date": "2024-04-20"},
]
cn_summary = {"total_cost": 192000.0, "total_value": 186610.0, "total_pnl": -5390.0, "total_pnl_pct": -2.81, "count": 2}
cn_diags = [
    {"code": "600000", "name": "浦发银行", "score": 60, "rating": "买入", "trend": "上升趋势", "price": 9.28, "change_pct": 0.98, "signals": [{"name": "突破20日新高"}, {"name": "RSI超买(81)"}], "risks": ["RSI超买", "高位钝化风险"]},
    {"code": "600519", "name": "贵州茅台", "score": 52, "rating": "观望", "trend": "震荡偏多", "price": 1680.50, "change_pct": -0.32, "signals": [], "risks": ["暂未触发明显风险信号"]},
    {"code": "000001", "name": "平安银行", "score": 58, "rating": "买入", "trend": "上升趋势", "price": 11.28, "change_pct": 0.71, "signals": [{"name": "MACD金叉"}], "risks": ["暂未触发明显风险信号"]},
    {"code": "601318", "name": "中国平安", "score": 55, "rating": "观望", "trend": "震荡偏多", "price": 48.62, "change_pct": 0.15, "signals": [{"name": "多头排列"}], "risks": ["暂未触发明显风险信号"]},
]
cn_scan = [
    {"code": "000333", "name": "美的集团", "close": 87.01, "change_pct": 1.92, "score": 50, "matched": ["多头排列", "突破新高", "放量"]},
    {"code": "000651", "name": "格力电器", "close": 41.65, "change_pct": 2.08, "score": 50, "matched": ["多头排列", "突破新高", "放量"]},
    {"code": "600036", "name": "招商银行", "close": 39.66, "change_pct": 0.18, "score": 35, "matched": ["多头排列", "突破新高"]},
    {"code": "002594", "name": "比亚迪", "close": 268.85, "change_pct": 1.61, "score": 20, "matched": ["突破新高"]},
]

# ---------- 美股 ----------
us_holdings = [
    {"code": "AAPL", "name": "Apple", "market": "US", "cost_price": 185.00, "quantity": 50,
     "current_price": 338.19, "market_value": 16909.50, "pnl": 7659.50, "pnl_pct": 82.81, "hold_days": 59, "buy_date": "2024-06-01"},
]
us_summary = {"total_cost": 9250.0, "total_value": 16909.50, "total_pnl": 7659.50, "total_pnl_pct": 82.81, "count": 1}
us_diags = [
    {"code": "AAPL", "name": "Apple", "score": 64, "rating": "买入", "trend": "上升趋势", "price": 338.19, "change_pct": -0.56, "signals": [], "risks": ["暂未触发明显风险信号"]},
    {"code": "TSLA", "name": "Tesla", "score": 42, "rating": "观望", "trend": "震荡偏空", "price": 248.50, "change_pct": -1.85, "signals": [{"name": "MACD死叉"}], "risks": ["MACD绿柱，动能偏弱"]},
    {"code": "MSFT", "name": "Microsoft", "score": 68, "rating": "买入", "trend": "上升趋势", "price": 445.20, "change_pct": 0.92, "signals": [{"name": "多头排列"}, {"name": "突破20日新高"}], "risks": ["暂未触发明显风险信号"]},
]

# ---------- 港股 ----------
hk_holdings = [
    {"code": "00700", "name": "腾讯控股", "market": "HK", "cost_price": 380.00, "quantity": 200,
     "current_price": 395.60, "market_value": 79120.0, "pnl": 3120.0, "pnl_pct": 4.11, "hold_days": 81, "buy_date": "2024-05-10"},
]
hk_summary = {"total_cost": 76000.0, "total_value": 79120.0, "total_pnl": 3120.0, "total_pnl_pct": 4.11, "count": 1}
hk_diags = [
    {"code": "00700", "name": "腾讯控股", "score": 61, "rating": "买入", "trend": "上升趋势", "price": 395.60, "change_pct": 1.42, "signals": [{"name": "多头排列"}], "risks": ["暂未触发明显风险信号"]},
    {"code": "09988", "name": "阿里巴巴", "score": 57, "rating": "买入", "trend": "震荡偏多", "price": 82.30, "change_pct": 2.15, "signals": [{"name": "MACD金叉"}, {"name": "放量"}], "risks": ["暂未触发明显风险信号"]},
]

def _inject_advice(diags):
    """给模拟诊断注入 ATR 建议价位(模拟ATR≈3%现价)。"""
    for d in diags:
        p = d["price"]
        r = d["rating"]
        atr = p * 0.03
        if r in ("强烈买入", "买入", "观望"):
            d["advice"] = {
                "action": "可买入" if r != "观望" else "观望待确认",
                "buy_price": round(p, 2),
                "stop_loss": round(p - 2 * atr, 2),
                "take_profit": round(p + 3 * atr, 2),
                "risk_reward": "1:1.5",
                "atr": round(atr, 3),
            }
        else:
            d["advice"] = {
                "action": "不建议买入",
                "buy_price": None,
                "stop_loss": round(p, 2),
                "take_profit": None,
                "risk_reward": "—",
                "atr": round(atr, 3),
            }

_inject_advice(cn_diags)
_inject_advice(us_diags)
_inject_advice(hk_diags)

# 美股/港股模拟扫描命中
us_scan = [
    {"code": "MSFT", "name": "Microsoft", "close": 445.20, "change_pct": 0.92, "score": 50, "matched": ["多头排列", "突破新高"]},
    {"code": "NVDA", "name": "NVIDIA", "close": 122.30, "change_pct": 3.15, "score": 35, "matched": ["突破新高", "放量"]},
    {"code": "GOOGL", "name": "Alphabet", "close": 178.50, "change_pct": 1.20, "score": 20, "matched": ["多头排列"]},
]
hk_scan = [
    {"code": "09988", "name": "阿里巴巴", "close": 82.30, "change_pct": 2.15, "score": 50, "matched": ["MACD金叉", "放量"]},
    {"code": "03690", "name": "美团", "close": 115.60, "change_pct": 1.85, "score": 35, "matched": ["突破新高"]},
    {"code": "06618", "name": "京东健康", "close": 38.20, "change_pct": 0.95, "score": 20, "matched": ["多头排列"]},
]

for market, diags, holdings, summary, scan in [
    ("CN", cn_diags, cn_holdings, cn_summary, cn_scan),
    ("US", us_diags, us_holdings, us_summary, us_scan),
    ("HK", hk_diags, hk_holdings, hk_summary, hk_scan),
]:
    title, text, html = build_market_message(
        market, diags, scan, scan_enabled=True,
        holdings=holdings, holdings_summary=summary,
    )
    out = f"results/email_{market.lower()}_preview.html"
    Path(out).write_text(html, encoding="utf-8")
    print(f"✓ {market}: {out}")
    print(f"  标题: {title}")
    print(f"  纯文本预览:\n{text[:300]}\n")
