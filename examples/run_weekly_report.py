"""手动立即生成 A股周报（PDF），可选邮件附件发送。

Usage::

    python examples/run_weekly_report.py                     # 持仓+漏斗Top10，发邮件
    python examples/run_weekly_report.py --no-send           # 只生成不发送
    python examples/run_weekly_report.py --stocks 600000,600519
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.weekly_report import run as weekly_run
from quant_trading_system.stock_analysis.holdings import Holdings
from quant_trading_system.stock_analysis.notifier import Notifier

DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 A股周报 PDF")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--stocks", default=None,
                    help="逗号分隔股票代码；默认 持仓 + 漏斗Top10")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--author", default="GP助手")
    ap.add_argument("--no-send", action="store_true", help="只生成 PDF，不发邮件")
    args = ap.parse_args()

    holdings = Holdings(str(Path(args.config).parent / "holdings.yaml")).all()
    codes = [c.strip() for c in args.stocks.split(",")] if args.stocks else None
    path = weekly_run.run_weekly_report(
        ROOT, stocks=codes, holdings=holdings,
        date=args.date, author=args.author,
    )
    print("周报已生成:", path)

    if not args.no_send:
        email_codes = codes or weekly_run.collect_codes(holdings, root=ROOT)
        title, text, html = weekly_run.report_email(path, email_codes)
        res = Notifier(args.config).send(
            title, text, html,
            attachments=[(path.name, path.read_bytes())],
        )
        print("邮件发送结果:", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
