"""成交记录粘贴入口 — 粘贴券商成交提醒文本，自动解析并写入持仓.

用法::

    # 交互模式：粘贴成交文本后按回车，再按 Ctrl-D 结束输入
    python examples/trade_input.py

    # 直接传入文本（或从剪贴板读取）
    python examples/trade_input.py "股票代码：600519 交易方向：买入 已成交100股"
    python examples/trade_input.py --clipboard

    # 只解析预览，不写入
    python examples/trade_input.py --preview

支持的格式（同花顺『成交提醒』、平安证券等）::

    成交提醒
    股票代码：    513310
    股票名称：    中韩半导体ETF华泰柏瑞
    交易方向：    买入，委托数量200股
    成交量：    已成交200股，已全部成交
    成交金额：    937.40元（成交价格：4.687元）

也可以直接粘贴『买入 600519 贵州茅台 100股 成交价1500.00』这类自由文本。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.trade_monitor import TradeMonitor

DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def _read_clipboard() -> str:
    try:
        out = subprocess.run(["pbpaste"], capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _read_stdin() -> str:
    print("请粘贴成交记录文本，粘贴完成后按回车，再按 Ctrl-D 结束输入：")
    print("-" * 60)
    try:
        return sys.stdin.read().strip()
    except KeyboardInterrupt:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="成交记录粘贴入口")
    parser.add_argument("text", nargs="?", default=None,
                        help="成交文本（不填则交互粘贴）")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--clipboard", action="store_true",
                        help="从剪贴板读取成交文本")
    parser.add_argument("--preview", action="store_true",
                        help="只解析预览，不写入持仓库")
    args = parser.parse_args()

    if args.clipboard:
        raw = _read_clipboard()
        if not raw:
            print("✘ 剪贴板为空")
            sys.exit(1)
    elif args.text:
        raw = args.text.strip()
    else:
        raw = _read_stdin()

    if not raw:
        print("✘ 未输入任何内容")
        sys.exit(1)

    mon = TradeMonitor(args.config)
    trade = mon.parser.parse(raw)

    if trade is None:
        print()
        print("✘ 未能识别出成交信息（需要包含 交易方向：买入/卖出 + 股票代码）")
        print("   支持的格式示例：")
        print("     ┌─ 成交提醒 ────────────────────────────┐")
        print("     │ 股票代码：513310                      │")
        print("     │ 交易方向：买入，委托数量200股          │")
        print("     │ 成交量：已成交200股，已全部成交        │")
        print("     │ 成交金额：937.40元（成交价格：4.687元）│")
        print("     └───────────────────────────────────────┘")
        print("   或自由文本：买入 600519 贵州茅台 100股 成交价1500.00")
        sys.exit(1)

    print()
    print("解析结果：")
    print(f"  方向：{'买入 ▶' if trade.side == 'BUY' else '卖出 ◀'}")
    print(f"  代码：{trade.code}")
    print(f"  名称：{trade.name or '（未知）'}")
    print(f"  数量：{int(trade.quantity) if trade.quantity else '（未识别）'} 股")
    print(f"  价格：{trade.price if trade.price else '（未识别）'}")
    if trade.ts:
        print(f"  时间：{trade.ts}")

    if args.preview:
        print()
        print("（--preview 模式，未写入持仓库）")
        return

    confirm = input("\n确认写入持仓库？(y/N): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("已取消")
        return

    action, msg = mon.apply_trade(trade)
    print()
    print(f"✔ [{action}] {msg}")

    # 显示更新后的持仓
    print()
    print("当前持仓：")
    for p in mon.holdings.all():
        qty = int(float(p["quantity"]))
        print(f"  {p['code']}  {p['name'] or ''}  {qty}股  "
              f"成本 {p['cost_price']:.4f}  {p['market']}")
    if mon.holdings.is_empty():
        print("  （空）")


if __name__ == "__main__":
    main()
