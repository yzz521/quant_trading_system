"""实时盯盘（快轨）— 盘中秒级轮询持仓/自选，命中规则才推送.

与 ``run_scheduler.py``（慢轨）的区别：
* 慢轨：每 30~60 分钟跑「全市场初筛 + 批量机会扫描 + 完整邮件」，回答"今天该关注哪些票"
* 快轨：只盯持仓/自选（几十只票 1 个批量请求），盘中默认 5 秒一轮，回答"现在要不要动手"

Usage::

    # 只看一轮结果，不推送（不需要开启 realtime.enabled，也不看交易时段）
    python examples/run_realtime.py --dry-run

    # 跑一轮并真实推送（验证通知渠道）
    python examples/run_realtime.py --once

    # 只看状态：在不在跑、跑到哪了（不起循环、不发请求）
    python examples/run_realtime.py --status

    # 常驻盯盘：开市中每 5 秒一轮，午休/休市降频到 60 秒
    python examples/run_realtime.py

先编辑 quant_trading_system/config/notify.yaml 填入通知凭证，并把
``realtime.enabled`` 设为 true。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.realtime import (  # noqa: E402
    _SEV_LABEL,
    RealtimeWatcher,
    in_session,
    install_sigterm_stop,
)

DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="实时盯盘引擎（快轨）")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="跑一轮（含推送）后退出")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送，忽略 enabled 与交易时段")
    parser.add_argument("--interval", type=int, default=0, help="覆盖盘中轮询间隔（秒）")
    parser.add_argument("--status", action="store_true", help="只看运行状态（在不在跑、跑到哪了）")
    args = parser.parse_args()

    watcher = RealtimeWatcher(args.config, dry_run=args.dry_run)
    if args.interval:
        watcher.interval = max(1, args.interval)

    if args.status:
        print(watcher.status_line())
        for k, v in watcher.status().items():
            print(f"  {k:<20} {v}")
        return

    print("=== 盯盘清单 ===")
    for t in watcher.watchlist():
        cost = f" 成本 {t['cost_price']}" if t.get("cost_price") else ""
        print(f"  {t['name']}({t['code']}) [{t['market']}]{cost}")
    print("=== 时段状态 ===")
    for m in ("CN", "HK", "US"):
        print(f"  {m}: {'盘中' if in_session(m, us_winter=watcher.us_winter) else '休市'}")
    print(f"已启用: {watcher.enabled} | 盘中间隔 {watcher.interval}s | 冷却 {watcher.cooldown / 60:.0f} 分钟")
    print("================")

    if args.once or args.dry_run:
        hits = watcher.tick(force=True)
        print(f"本轮命中并推送 {len(hits)} 条")
        for a in hits:
            print(f"  [{_SEV_LABEL[a.severity]}] {a.name}({a.code}) {a.title} — {a.detail}")
    else:
        install_sigterm_stop(watcher)   # ctl.py stop 发 SIGTERM，让它优雅退出
        watcher.run_forever()


if __name__ == "__main__":
    main()
