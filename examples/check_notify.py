"""配置与调度器自检：持仓数量、notify 渠道、最近运行状态。

Usage::

    python examples/check_notify.py
    python examples/check_notify.py --send-test   # 真正发一封测试（需渠道启用）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.utils import load_yaml
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.scheduler_state import format_status_text, load_state
from quant_trading_system.stock_analysis.notifier import Notifier


DEFAULT_NOTIFY = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def main() -> int:
    ap = argparse.ArgumentParser(description="notify / 持仓 / 调度状态自检")
    ap.add_argument("--config", default=DEFAULT_NOTIFY)
    ap.add_argument("--send-test", action="store_true", help="发送一封测试消息")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    print("=== 配置自检 ===")
    print(f"notify 配置: {cfg_path}  存在={cfg_path.exists()}")
    if not cfg_path.exists():
        print("❌ 请先: cp config/notify.yaml.example config/notify.yaml 并填写凭证")
        print(format_status_text())
        return 1

    cfg = load_yaml(str(cfg_path)) or {}
    notify = cfg.get("notify") or {}
    enabled = [k for k, v in notify.items() if isinstance(v, dict) and v.get("enabled")]
    print(f"启用渠道: {enabled or '无（将只打印到日志）'}")
    if not enabled:
        print("⚠️  所有渠道 enabled=false，调度器会跑分析但不会发邮件/微信")

    markets = cfg.get("enabled_markets") or ["CN", "HK", "US"]
    print(f"启用市场: {markets}")

    holdings_path = str(cfg_path.parent / "holdings.yaml")
    h = Holdings(holdings_path)
    rows = h.all()
    print(f"持仓库: {h.db_path}  持仓只数={len(rows)}")
    if rows:
        for r in rows[:10]:
            print(f"  - {r.get('code')} {r.get('name')} {r.get('market')} "
                  f"成本{r.get('cost_price')} 数量{r.get('quantity')}")
        if len(rows) > 10:
            print(f"  ... 另有 {len(rows)-10} 只")
    else:
        print("⚠️  持仓为空：看板录入或确认 config/holdings.db")

    pools = cfg.get("stock_pools") or {}
    for m in markets:
        print(f"  自选池[{m}]: {len(pools.get(m) or [])} 只")

    print()
    print(format_status_text())

    if args.send_test:
        if not enabled:
            print("❌ 无启用渠道，无法 --send-test")
            return 1
        n = Notifier(str(cfg_path))
        r = n.send(
            "GP分析助手 · 配置自检",
            f"自检成功。持仓 {len(rows)} 只。渠道: {enabled}",
            html=f"<p>自检成功。</p><p>持仓 <b>{len(rows)}</b> 只。</p><p>渠道: {enabled}</p>",
        )
        print("发送结果:", r)

    print("\n建议下一步:")
    print("  python examples/run_scheduler.py --test --market CN")
    print("  python deploy/ctl.py scheduler start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
