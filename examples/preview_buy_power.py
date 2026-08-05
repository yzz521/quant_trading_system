"""预览资金约束标注（不发邮件）。

    python examples/preview_buy_power.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.buy_power import annotate_list, partition_annotated

ROOT = Path(__file__).resolve().parents[1]
h = Holdings(str(ROOT / "config" / "holdings.yaml"))
print("账户:", h.get_account())
print("快照:", h.capital_snapshot())
print("持仓数:", len(h.all()), "占用成本:", h.invested_cost())

fake = [
    {"code": "600519", "name": "贵州茅台", "close": 1700, "change_pct": 0.5, "score": 80, "matched": ["多头"]},
    {"code": "000001", "name": "平安银行", "close": 11.5, "change_pct": -0.2, "score": 70, "matched": ["超卖"]},
    {"code": "600036", "name": "招商银行", "close": 35.0, "change_pct": 1.0, "score": 75, "matched": ["放量"]},
]
snap, ann = annotate_list(fake, holdings_mgr=h, price_key="close", default_market="CN")
if snap is None:
    print("未设置总资金，请在看板设置（如 10000）后再预览")
else:
    parts = partition_annotated(ann)
    print("--- 可买 ---")
    for x in parts["ok"]:
        print(x["code"], x.get("buy_label"))
    print("--- 资金不足等 ---")
    for x in parts["no_cash"] + parts["held"] + parts["capped"]:
        print(x["code"], x.get("buy_label"))
