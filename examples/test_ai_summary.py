"""试跑 AI 点评（不发邮件）。

  export QTS_AI_API_KEY=sk-...
  python examples/test_ai_summary.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.utils import load_yaml
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.holdings_action import analyze_holding_actions
from quant_trading_system.stock_analysis.ai_summary import generate_market_summary

ROOT = Path(__file__).resolve().parents[1]
cfg = load_yaml(str(ROOT / "config" / "notify.yaml")) or {}
h = Holdings(str(ROOT / "config" / "holdings.yaml"))
rows = h.all()
try:
    actions = analyze_holding_actions(rows) if rows else []
except Exception:
    actions = []
text = generate_market_summary(
    cfg,
    market="CN",
    holdings=rows,
    holdings_summary={"count": len(rows)},
    holding_actions=actions,
    capital_snapshot=h.capital_snapshot() if hasattr(h, "capital_snapshot") else None,
    diagnoses=[],
    scan_hits=[],
)
print(text or "(未生成：请检查 ai.enabled 与 api_key)")
