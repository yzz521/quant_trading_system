"""个股深度诊断 — 真实股票分析，生成 HTML 报告.

Usage::

    python examples/diagnose_stock.py              # 默认 600000
    python examples/diagnose_stock.py 600519        # 指定 A 股
    python examples/diagnose_stock.py AAPL          # 美股
    python examples/diagnose_stock.py 00700         # 港股
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis import (
    StockDiagnoser, StockReport, detect_market, fetch_kline,
)


def main(code: str = "600000") -> None:
    print(f"正在分析 {code} ...")
    diag = StockDiagnoser()
    result = diag.diagnose(code)

    # 拉一份行情用于 K 线图
    info = detect_market(code)
    df = fetch_kline(info, days=120)

    report = StockReport(result, df)
    path = report.to_html(f"results/diagnose_{code}.html")

    print("\n" + "=" * 60)
    print(result.summary)
    print("=" * 60)
    print(f"综合评分: {result.score}/100   评级: {result.rating}   趋势: {result.trend}")
    print(f"当前价: {result.price}  涨跌: {result.change_pct:+.2f}%")
    print(f"形态信号: {[s['name'] for s in result.signals] or '无'}")
    print(f"风险提示: {result.risks}")
    print("=" * 60)
    print(f"诊断报告已保存: {path}")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600000"
    main(code)
