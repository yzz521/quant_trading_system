"""交易监听 — 从 macOS 通知中心识别券商成交消息，自动同步持仓.

监听 同花顺 / 平安证券 通过微信服务号推送的成交回报，解析出买卖方向、
股票代码、数量、价格后自动更新 config/holdings.db（与持仓管理页面联动）。

前提（重要）
-----------
1. 系统设置 → 隐私与安全性 → 完全磁盘访问
   给当前终端 / IDE 勾选授权（否则读不到通知中心数据库）。
2. 微信 Mac 版保持登录在线（服务号消息才会进通知中心）。
3. 同花顺 / 平安证券 的微信服务号开启"交易提醒 / 成交回报"推送。

Usage::

    # 自测解析逻辑（不依赖授权，验证正则/成本算法）
    python examples/run_trade_monitor.py --self-test

    # 执行一次（查看最近 48h 内的成交通知并同步持仓）
    python examples/run_trade_monitor.py --once

    # 只解析不写库（演练模式，看会识别出什么）
    python examples/run_trade_monitor.py --once --dry-run

    # 常驻监听（每 30 秒轮询一次，推荐用 deploy/ctl.sh 类似方式后台跑）
    python examples/run_trade_monitor.py

先编辑 config/notify.yaml 的 trade_monitor 段（默认已配好常用项）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant_trading_system.stock_analysis.trade_monitor import TradeMonitor, self_test

DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "notify.yaml")


def _fmt_applied(applied: list[dict]) -> str:
    return "\n".join(f"  ✔ {a['message']}" for a in applied) if applied else "  （无）"


def main() -> None:
    parser = argparse.ArgumentParser(description="交易监听：通知中心→持仓库")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--once", action="store_true",
                        help="只执行一次（默认常驻轮询）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只解析不写入持仓库")
    parser.add_argument("--self-test", action="store_true",
                        help="运行解析/成本算法自测（无需授权）")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(self_test())

    mon = TradeMonitor(args.config)
    if args.dry_run:
        mon.auto_sync = False
        # dry-run 时不写 trade_log，避免污染审计；仅打印识别结果
        from quant_trading_system.stock_analysis.trade_monitor import NotificationReader
        try:
            notifs = mon.reader.fetch_new(0, app_filter=mon.app_filter,
                                          lookback_min=mon.lookback_min)
        except Exception as e:  # noqa: BLE001
            print(f"✘ 读取通知中心失败: {e}")
            sys.exit(1)
        print(f"=== 演练模式（最近 {mon.lookback_min} 分钟，共 {len(notifs)} 条通知） ===")
        if notifs:
            newest = NotificationReader.to_beijing(notifs[0].get("date"))
            oldest = NotificationReader.to_beijing(notifs[-1].get("date"))
            print(f"通知时间范围: {oldest} ~ {newest}（最新 rec_id={notifs[0].get('rec_id')}）")
        hits = 0
        hidden = 0
        for n in notifs:
            ts = NotificationReader.to_beijing(n.get("date"))
            raw = f"{n.get('title') or ''} {n.get('body') or ''}".strip()
            if any(m in raw for m in mon._HIDDEN_BODY_MARKERS):
                hidden += 1
            t = mon.parser.parse(raw, ts=ts)
            if t:
                hits += 1
                print(f"  ➜ {t.side} {t.code} {t.name or ''} "
                      f"{int(t.quantity) if t.quantity else '?'}股 "
                      f"@{t.price or '?'}  [{ts}]")
                print(f"    原文: {raw[:120]}")
        print(f"识别出 {hits} 条疑似成交消息（未写入持仓库）")
        print()
        print("使用提示：")
        print("  1) 微信服务号成交回报默认不进 macOS 通知中心，监听不到是正常的。")
        print("  2) 推荐做法：成交后把券商 App 的成交文本复制粘贴发送到微信")
        print("     『文件传输助手』（好友消息会进通知中心），程序即可自动识别。")
        print("  3) 可识别的文本示例（含 买入/卖出 + 股票代码 即可）：")
        print("     『证券买入 600519 贵州茅台 100股 成交价1500.00』")
        print("     『卖出 000001 平安银行（000001）200股 12.50元』")
        print("  4) 测试：把上面任一条发到文件传输助手，再运行本命令即可看到识别。")
        if hidden and not hits:
            print()
            print("⚠ 检测到部分微信通知是占位文案（『你收到了一条消息』）——")
            print("   这些是之前预览关闭时存入的历史通知；预览设置后新消息已是完整内容。")
        return

    if args.once:
        print("=== 执行一次 ===")
        applied, skipped = mon.process_once()
        print("已同步:")
        print(_fmt_applied(applied))
        errs = [s for s in skipped if s["action"] == "ERROR"]
        if errs:
            print("错误:")
            for e in errs:
                print(f"  ✘ {e['message']}")
        return

    mon.run_forever()


if __name__ == "__main__":
    main()
