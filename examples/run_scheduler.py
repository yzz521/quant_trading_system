"""自动分析推送调度器 — 开盘期间定时执行，结果推送到邮箱/微信/飞书.

Usage::

    # 测试一次（立即执行当前开盘市场的分析，验证通知渠道）
    python examples/run_scheduler.py --test

    # 测试指定市场（不论是否开盘）
    python examples/run_scheduler.py --test --market CN

    # 常驻运行（A股每小时、美股港股每10分钟，仅交易时段）
    python examples/run_scheduler.py

先编辑 quant_trading_system/config/notify.yaml 填入通知凭证。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import MarketScheduler

# 默认配置用绝对路径，从任意目录运行都能定位到 quant_trading_system/config/notify.yaml
DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="股票自动分析推送调度器")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--test", action="store_true", help="立即执行一次（不等调度）")
    parser.add_argument("--market", default=None, choices=["CN", "US", "HK", None],
                        help="测试时指定市场（默认所有开盘市场）")
    parser.add_argument("--once-daily", action="store_true",
                        help="立即执行一次收盘漏斗（全市场四层过滤 + 推送）")
    args = parser.parse_args()

    scheduler = MarketScheduler(args.config)

    if args.once_daily:
        print("=== 收盘漏斗模式（全市场四层过滤） ===")
        scheduler.run_funnel_once()
    elif args.test:
        print("=== 当前时段状态 ===")
        for m, open_ in scheduler.session_status().items():
            print(f"  {m}: {'开盘' if open_ else '休市'}")
        print("======================")
        scheduler.run_once(args.market)
    else:
        print("=== 常驻运行模式 ===")
        for m, open_ in scheduler.session_status().items():
            print(f"  {m}: {'开盘' if open_ else '休市'}")
        scheduler.run_forever()


if __name__ == "__main__":
    main()
