"""成交与成本模型 —— 让回测回答「这笔单子真的能成交、真的扣了费吗」。

背景
----
2026-09 的回测可信度审计（``examples/audit_backtest_realism.py``）发现：原 ``_simulate``
只在 ``low <= entry_high`` 时判定成交，成交价却取现价**之下**的支撑位，缺少
「当日是否真跌到 entry_price」这一步 → 系统性以**买不到的幽灵价**建仓；同时完全没有
佣金/印花税/滑点、跳空穿透、涨跌停与停牌约束。结果是回测每笔 +2.59% 的正期望里
绝大部分来自不可实现的成交价。

本模块把「可成交性」拆成四个可独立开关的口径：

1. **限价成交可达性**：限价买入只在价格真的触及委托价时成交。
2. **交易成本**：佣金（双边）+ 印花税（卖出）+ 过户费 + 滑点，按市场预设。
3. **涨跌停 / 停牌**：一字涨停买不进、一字跌停卖不出；停牌日不成交。
4. **容量约束**：单日最多吃掉当日成交额的固定比例，超出部分按比例缩量。

用法::

    from quant_trading_system.stock_analysis.backtest.execution import (
        ExecutionConfig, cost_model_for_market,
    )
    cfg = ExecutionConfig(cost_model=cost_model_for_market("CN"), participation_rate=0.10)
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

# 1 bp = 0.01%
_BPS = 1e-4


@dataclass(frozen=True)
class CostModel:
    """单笔交易的成本模型（费率单位：bp，1 bp = 0.01%）。

    Attributes:
        commission_bps: 佣金费率（双边各收一次）。
        min_commission: 单笔最低佣金（元）。仅在已知订单金额时生效。
        stamp_tax_bps: 印花税（**仅卖出**收取）。
        transfer_fee_bps: 过户费（双边各收一次）。
        slippage_bps: 滑点（双边各收一次），代表冲击成本与买卖价差。
    """

    commission_bps: float = 2.5
    min_commission: float = 5.0
    stamp_tax_bps: float = 5.0
    transfer_fee_bps: float = 0.1
    slippage_bps: float = 5.0

    def buy_cost_pct(self, price: float) -> float:
        """买入侧成本（占成交价的百分比）。"""
        if price is None or price <= 0:
            return 0.0
        return (self.commission_bps + self.transfer_fee_bps + self.slippage_bps) * _BPS * 100

    def sell_cost_pct(self, price: float) -> float:
        """卖出侧成本（占成交价的百分比，含印花税）。"""
        if price is None or price <= 0:
            return 0.0
        return (
            self.commission_bps + self.transfer_fee_bps + self.stamp_tax_bps + self.slippage_bps
        ) * _BPS * 100

    def round_trip_cost_pct(
        self,
        entry_price: float,
        exit_price: float,
        *,
        notional: Optional[float] = None,
    ) -> float:
        """一次完整买卖的成本（占**入场价**的百分比）。

        买入成本按入场价计、卖出成本按出场价计，再统一折算到入场价上，
        这样 ``return_pct - round_trip_cost_pct`` 就是可直接比较的净收益。

        Args:
            notional: 订单金额（元）。给了才会把**最低佣金**算进去——最低佣金是
                按笔收取的固定费用，只有知道这一笔投了多少钱才能判断费率是否
                触及下限。小额订单（如 1000 元）实际佣金率可达万 2.5 的 20 倍，
                不算进来会系统性高估小账户/小仓位的净收益。
        """
        if entry_price is None or entry_price <= 0:
            return 0.0
        exit_price = exit_price if (exit_price and exit_price > 0) else entry_price
        buy_pct = self.buy_cost_pct(entry_price)
        sell_pct = self.sell_cost_pct(exit_price)
        if notional is not None and notional > 0:
            extra = self.min_commission_pct(notional)
            buy_pct += extra          # 买入腿的最低佣金补差
            sell_pct += extra         # 卖出腿的最低佣金补差
        buy = buy_pct / 100.0 * entry_price
        sell = sell_pct / 100.0 * exit_price
        return (buy + sell) / entry_price * 100.0

    def min_commission_pct(self, notional: float) -> float:
        """按订单金额折算最低佣金的影响（占订单金额百分比）。

        返回的是「实际佣金率 − 名义费率」的**补差**，0 表示未触及最低佣金。
        """
        if notional is None or notional <= 0 or self.min_commission <= 0:
            return 0.0
        rate_pct = self.commission_bps * _BPS * 100
        actual_pct = max(rate_pct, self.min_commission / notional * 100)
        return actual_pct - rate_pct


# 市场预设：费率取自各市场公开规则（2024 年口径），滑点为保守估计
MARKET_COST_PRESETS: dict[str, CostModel] = {
    # A 股：佣金万 2.5（最低 5 元）、印花税卖出 0.05%、过户费 0.001%、滑点 0.05%
    "CN": CostModel(
        commission_bps=2.5, min_commission=5.0, stamp_tax_bps=5.0,
        transfer_fee_bps=0.1, slippage_bps=5.0,
    ),
    # 港股：佣金万 2.5、印花税双边 0.1%、交易费 0.00565%、滑点 0.1%
    "HK": CostModel(
        commission_bps=2.5, min_commission=3.0, stamp_tax_bps=10.0,
        transfer_fee_bps=0.565, slippage_bps=10.0,
    ),
    # 美股：零佣金、无印花税、滑点 0.03%
    "US": CostModel(
        commission_bps=0.0, min_commission=0.0, stamp_tax_bps=0.0,
        transfer_fee_bps=0.0, slippage_bps=3.0,
    ),
}


def cost_model_for_market(market: str) -> CostModel:
    """按市场取成本预设；未知市场回退 A 股口径（保守）。"""
    return MARKET_COST_PRESETS.get(str(market or "CN").upper(), MARKET_COST_PRESETS["CN"])


# A 股涨跌停幅度：主板 10%，创业板/科创板 20%，北交所 30%，ST 5%
LIMIT_PCT_DEFAULT = 0.10
LIMIT_PCT_STAR = 0.20
LIMIT_PCT_BJ = 0.30
LIMIT_PCT_ST = 0.05

# 北交所代码前缀（43/83/87 段 + 2023 年新增的 920 段）
_BJ_PREFIXES = ("43", "83", "87", "92")

# 哪些市场存在「日内涨跌停封板」这种制度。港股、美股都没有 A 股式涨跌停，
# 若沿用 10% 口径，会把「当天涨了 10%」的正常美股误判成一字涨停而拒绝成交。
MARKET_HAS_PRICE_LIMITS: dict[str, bool] = {"CN": True, "HK": False, "US": False}


def market_has_price_limits(market: str) -> bool:
    """该市场是否有日内涨跌停封板制度（未知市场保守按「有」处理）。"""
    return MARKET_HAS_PRICE_LIMITS.get(str(market or "CN").upper(), True)


def limit_pct_for_code(code: str, *, is_st: bool = False) -> float:
    """按代码推断涨跌停幅度（A 股）。

    优先级：ST 5% → 创业板 300/301 与科创板 688/689 20% → 北交所 30% → 主板 10%。

    Args:
        code: 股票代码。
        is_st: 是否风险警示股（ST/*ST）。ST 由名称标记而非代码决定，调用方需显式传入。
    """
    if is_st:
        return LIMIT_PCT_ST
    c = str(code or "").strip()
    if c.startswith(("300", "301", "688", "689")):
        return LIMIT_PCT_STAR
    if c.startswith(_BJ_PREFIXES):
        return LIMIT_PCT_BJ
    return LIMIT_PCT_DEFAULT


@dataclass
class ExecutionConfig:
    """回测的成交假设配置。

    Attributes:
        cost_model: 成本模型；None 时用 A 股预设。
        participation_rate: 单日最多吃掉的当日成交额比例（容量约束）。
        enforce_price_limits: 是否启用涨跌停封板约束。
        enforce_suspension: 是否启用停牌（零成交）约束。
        gap_aware: 是否按跳空口径成交（止损取 min(开盘, 止损价)）。
        max_position_pct: 单票仓位上限（容量与组合曲线用）。
        eps: 浮点比较容差（相对值）。
    """

    cost_model: CostModel = field(default_factory=lambda: MARKET_COST_PRESETS["CN"])
    participation_rate: float = 0.10
    enforce_price_limits: bool = True
    enforce_suspension: bool = True
    gap_aware: bool = True
    max_position_pct: float = 0.20
    eps: float = 1e-6
    is_st: bool = False
    """是否风险警示股（ST/*ST）—— 只影响 A 股涨跌停幅度（5%）。"""

    def with_market(self, market: str) -> "ExecutionConfig":
        """返回同配置但成本模型与涨跌停口径换成指定市场的副本。

        ⚠️ 关键修正：**港股与美股没有 A 股式日内涨跌停**。原实现让所有市场都走
        ``LIMIT_PCT_DEFAULT=0.10``，于是美股某天涨 10% 会被 ``price_limit_state``
        判成「一字涨停 → 买不进」，港股同理。这里按市场自动关闭该约束。
        调用方仍可显式传 ``enforce_price_limits`` 覆盖（例如做敏感性测试）。
        """
        return ExecutionConfig(
            cost_model=cost_model_for_market(market),
            participation_rate=self.participation_rate,
            enforce_price_limits=(
                self.enforce_price_limits and market_has_price_limits(market)
            ),
            enforce_suspension=self.enforce_suspension,
            gap_aware=self.gap_aware,
            max_position_pct=self.max_position_pct,
            eps=self.eps,
            is_st=self.is_st,
        )


def apply_market(cfg: ExecutionConfig, market: str) -> ExecutionConfig:
    """按市场修正成交假设（涨跌停口径 + 成本模型）。

    规则：
      * **涨跌停口径总是按市场修正** —— 港股/美股没有 A 股式日内涨跌停，沿用
        10% 会把「正常涨 10%」误判成一字涨停而拒单。这一条不可被调用方绕过。
      * **成本模型**：调用方若显式自定义了费率（不等于 A 股预设），保留其设置；
        否则换成该市场的预设。这样「自定义费率」与「按市场取默认」都成立。

    为什么需要它：``TradingPlanBacktest`` 原先只在**没传** ``exec_config`` 时
    才调用 ``with_market``，于是任何显式传配置的调用方（测试、看板、脚本）都会
    静默拿到 A 股口径 —— 这正是「美股回测被 10% 封板拦单」的根因。
    """
    out = cfg.with_market(market)
    if cfg.cost_model != MARKET_COST_PRESETS["CN"]:
        out = replace(out, cost_model=cfg.cost_model)
    return out


def is_suspended(volume: Optional[float], amount: Optional[float]) -> bool:
    """停牌判定：成交量为 0（或成交量为缺失但成交额为 0）。

    两个字段**都缺失**时返回 ``False`` —— 数据源没给成交信息不等于停牌，
    否则会把整段行情误判成停牌而完全无法回测。
    """
    vol = _f(volume)
    amt = _f(amount)
    if vol is not None:
        return vol <= 0
    if amt is not None:
        return amt <= 0
    return False


def price_limit_state(
    prev_close: Optional[float],
    high: Optional[float],
    low: Optional[float],
    *,
    limit_pct: float = LIMIT_PCT_DEFAULT,
) -> int:
    """涨跌停封板状态。

    Returns:
        ``1`` 一字涨停（买不进） / ``-1`` 一字跌停（卖不出） / ``0`` 可正常成交。

    判据用「全天最低价仍在涨停价附近」而不是「最高价==最低价」，因为真实数据里
    封板日经常有 1 分钱的振幅（如 10.99/11.00），用严格相等会漏判。
    """
    pc = _f(prev_close)
    h, lo = _f(high), _f(low)
    if pc is None or pc <= 0 or h is None or lo is None:
        return 0
    up = round(pc * (1 + limit_pct), 2)
    down = round(pc * (1 - limit_pct), 2)
    if up > 0 and lo >= up * (1 - 0.002):
        return 1
    if down > 0 and h <= down * (1 + 0.002):
        return -1
    return 0


def limit_fill_price(prev_close: float, limit_pct: float, side: str) -> Optional[float]:
    """涨停价 / 跌停价（用于封板日的理论成交价，一般成交不了，仅作兜底记录）。"""
    pc = _f(prev_close)
    if pc is None or pc <= 0:
        return None
    mult = 1 + limit_pct if side == "up" else 1 - limit_pct
    return round(pc * mult, 2)


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v  # NaN 检查
    except (TypeError, ValueError):
        return None
