"""命令行：导出并请求本地 Vibe 二次分析。

  # 终端1
  vibe-trading serve --port 8899

  # 终端2
  python examples/run_vibe_secondary.py

  # 不带扫描候选
  python examples/run_vibe_secondary.py --no-candidates
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.vibe_bridge import (
    build_payload, load_latest_scan, submit_secondary_analysis, DEFAULT_BASE,
)

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="GP助手 → 本地 Vibe 二次分析")
parser.add_argument("--no-candidates", action="store_true",
                    help="不带扫描候选，只发持仓")
parser.add_argument("--candidate-count", type=int, default=15)
args = parser.parse_args()

h = Holdings(str(ROOT / "config" / "holdings.yaml"))
rows = h.all()
actions = []
try:
    from quant_trading_system.stock_analysis.holdings_action import analyze_holding_actions
    actions = analyze_holding_actions(rows) if rows else []
except Exception:
    pass

candidates = []
if not args.no_candidates:
    latest = load_latest_scan(ROOT)
    candidates = (latest.get("hits") or [])[: max(args.candidate_count, 1)]

payload = build_payload(
    holdings=rows,
    holding_actions=actions,
    capital_snapshot=h.capital_snapshot() if hasattr(h, "capital_snapshot") else None,
    candidates=candidates,
)
print("holdings:", len(rows))
print("candidates:", len(candidates))
r = submit_secondary_analysis(payload, root=ROOT, base_url=DEFAULT_BASE)
print("ok:", r.get("ok"))
print("error:", r.get("error"))
print("summary:\n", (r.get("summary") or "")[:2000])
print("saved:", r.get("result_path"))
