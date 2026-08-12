"""仓位管理 —— 把账户风险与止损距离绑定，计算建议仓位。

流程（计划书 §14）：
  账户资金 → 单笔最大亏损额 → 每股最大风险 → 最大股数 → 建议仓位。
单只股票不可无限加仓，仓位上限受市场环境调节（市场状态在外部传入）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class PositionSizing:
    """仓位建议结果。"""

    max_loss_amount: Optional[float] = None  # 单笔最大亏损额（元）
    risk_per_share: Optional[float] = None   # 每股风险（元）
    max_shares: Optional[int] = None         # 最大可买股数（手）
    suggested_shares: Optional[int] = None   # 建议股数（手）
    position_amount: Optional[float] = None  # 建议投入金额（元）
    position_percent: Optional[float] = None # 占总资产比例（%）
    capped: bool = False                     # 是否被单票上限约束

    def to_dict(self) -> dict:
        return {
            "max_loss_amount": self.max_loss_amount,
            "risk_per_share": self.risk_per_share,
            "max_shares": self.max_shares,
            "suggested_shares": self.suggested_shares,
            "position_amount": self.position_amount,
            "position_percent": self.position_percent,
            "capped": self.capped,
        }


def calc_position_size(
    account_equity: float,
    entry_price: float,
    stop_loss: float,
    risk_percent: float = 0.02,
    max_position_pct: float = 0.20,
    market_factor: float = 1.0,
    lot_size: int = 100,
) -> PositionSizing:
    """计算建议仓位。

    Args:
        account_equity: 账户总资金（元）。
        entry_price: 入场价。
        stop_loss: 止损价。
        risk_percent: 单笔风险占账户比例（默认 2%）。
        max_position_pct: 单票最大仓位占比（默认 20%）。
        market_factor: 市场环境调节系数（BULL=1.0 / NEUTRAL=0.75 / BEAR=0.5 / HIGH_RISK=0.25）。
        lot_size: A股一手股数（100）。
    """
    if not all(v is not None and v > 0 for v in (account_equity, entry_price)):
        return PositionSizing()

    risk_per_share = max(entry_price - stop_loss, entry_price * 0.01)  # 至少 1% 风险
    max_loss_amount = account_equity * risk_percent * market_factor

    # 最大股数（按手取整向下）
    max_shares_raw = max_loss_amount / risk_per_share
    max_shares = int(np.floor(max_shares_raw / lot_size)) * lot_size
    max_shares = max(max_shares, 0)

    # 仓位上限约束
    cap_amount = account_equity * max_position_pct
    cap_shares = int(np.floor(cap_amount / entry_price / lot_size)) * lot_size

    suggested = min(max_shares, cap_shares) if (max_shares and cap_shares) else max(max_shares, cap_shares)
    suggested = max(suggested, 0)
    capped = bool(cap_shares < max_shares) and cap_shares > 0

    position_amount = round(suggested * entry_price, 2)
    position_percent = round(position_amount / account_equity * 100, 2) if account_equity else 0.0

    return PositionSizing(
        max_loss_amount=round(max_loss_amount, 2),
        risk_per_share=round(risk_per_share, 2),
        max_shares=max_shares,
        suggested_shares=suggested,
        position_amount=position_amount,
        position_percent=position_percent,
        capped=capped,
    )
