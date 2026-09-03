"""Planned investment capital — holdings.db is the source of truth."""
from __future__ import annotations

from quant_trading_system.dashboard.paths import holdings_config, notify_config
from quant_trading_system.stock_analysis.app_config import save_app_config
from quant_trading_system.stock_analysis.holdings import Holdings


def planned_capital() -> float:
    """预计投入（元）。<=0 表示尚未设置。"""
    acc = Holdings(holdings_config()).get_account()
    return float(acc.get("total_capital") or 0)


def save_planned_capital(amount: float) -> float:
    """写入持仓资金账户，并同步 notify.yaml 供邮件仓位计算。"""
    cap = max(0.0, float(amount))
    Holdings(holdings_config()).set_account(total_capital=cap)
    save_app_config(notify_config(), {"opportunity": {"account_equity": cap}})
    return cap
