"""影子模式：把交易计划转成「订单意图」，但**不发送任何指令**。

为什么需要
----------
「自动化交易」的最后一步是下单。但在没有验证之前直接接券商风险极高
（见 2026-10-09 的决策审查：前置条件 6/7 要求先跑通影子模式与风控闸门）。
本模块把**执行层完整地跑一遍** —— 生成订单、过风控、落盘 —— 唯独不把订单发出去。
这样能在零资金风险的前提下，暴露执行层的全部逻辑缺陷。

设计
----
* **订单意图（``OrderIntent``）**：一条「假如要下单，会是什么」的完整记录 ——
  代码 / 方向 / 限价 / 数量 / 金额 / 止损 / 目标 / 触发了哪条闸门 / 状态。
* **5 道风控闸门**（对应决策条件 7，全部纯函数、可单测）：
    1. ``daily_loss``      单日最大亏损上限 —— 当日亏损超限则停止当日所有新开仓
    2. ``single_position`` 单票上限        —— 单只票市值不得超过总资产的一定比例
    3. ``total_position``  总仓位上限      —— 全部持仓市值不得超过总资产的一定比例
    4. ``connection``      断线即停        —— 行情连接断开或数据过期，一律不下单
    5. ``duplicate``       幂等防重复下单  —— 同一 (日期, 代码, 方向) 只能有一条订单
* **append-only 台账**：``results/shadow_orders.jsonl``，写入后不改，天然可审计。
* **对账**：``reconcile_with_signals`` 校验「执行层认为要下的单」与「信号层记录的可执行
  信号」**完全一致** —— 两边口径分叉是执行层最隐蔽的 bug，必须由代码来查而不是靠人眼。

安全边界
--------
本模块**没有任何下单能力**，也**不导入任何券商接口**。``OrderIntent.shadow`` 恒为 True，
仅用于在台账与报告里显式标注「这条不是真实委托」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..utils import get_logger

log = get_logger("ShadowOrders")

#: 会被转成订单意图的决策（与 ``paper_tracking.TRACKED_DECISIONS`` 口径一致）
ACTIONABLE_DECISIONS = ("BUY_NOW", "BUY_ON_PULLBACK")

#: 成本占「止损距离」的比例超过它 → 成本警告（决策条件 4 的量化形式）
COST_TO_RISK_WARN = 1.0 / 3.0

#: 拦截原因**瞬时**的闸门：断线、当日亏损。它们只进日志，不写台账
#: （理由见 ``recordable``）。其余闸门（单票/总仓位/重复）是持久或已存在的状态，照常入账。
TRANSIENT_GUARDS = ("connection", "daily_loss")

_RESULTS = Path(__file__).resolve().parents[1] / "results"


# --------------------------------------------------------------------------- #
# 风控闸门参数
# --------------------------------------------------------------------------- #
@dataclass
class RiskGuardConfig:
    """执行层风控闸门参数。默认值保守；每一项都可被单测单独打开/关闭。"""

    max_daily_loss_pct: float = 0.03     # 当日亏损 > 总资产 3% → 停止当日新开仓
    max_single_pct: float = 0.20         # 单票市值上限（与 position_sizing 一致）
    max_total_pct: float = 0.95          # 总持仓市值上限（留出费用与滑点余量）
    max_quote_staleness_sec: int = 120   # 行情超过这么久未更新 → 视为断线
    lot_size: int = 100                  # A 股一手

    def to_dict(self) -> dict:
        return {
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_single_pct": self.max_single_pct,
            "max_total_pct": self.max_total_pct,
            "max_quote_staleness_sec": self.max_quote_staleness_sec,
            "lot_size": self.lot_size,
        }


# --------------------------------------------------------------------------- #
# 订单意图
# --------------------------------------------------------------------------- #
@dataclass
class OrderIntent:
    """一条「假如要下单，会是什么」的记录。``shadow`` 恒为 True。"""

    code: str = ""
    name: str = ""
    side: str = "BUY"                     # BUY / SELL
    order_type: str = "LIMIT"
    price: Optional[float] = None         # 限价
    quantity: int = 0                     # 股（已按手取整）
    amount: Optional[float] = None        # 金额（元）

    decision: str = ""
    position_percent: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    risk_reward_1: Optional[float] = None

    #: READY = 若无障碍会发出；BLOCKED = 被某道闸门拦下；SKIPPED = 本来就不该下单
    status: str = "READY"
    blocked_by: str = ""                  # status=BLOCKED 时，触发闸门的名字
    notes: list[str] = field(default_factory=list)

    # 成本：**信息性**，不是闸门（闸门只有 5 道，见模块 docstring）
    cost_pct: Optional[float] = None      # 双边成本（占入场价百分比）
    cost_to_risk: Optional[float] = None  # 成本 / 止损距离
    cost_warning: bool = False

    date: str = ""
    generated_at: str = ""
    shadow: bool = True                   # 恒为 True —— 本模块永不发送订单

    @property
    def key(self) -> tuple[str, str, str]:
        """幂等键：同一 (日期, 代码, 方向) 只允许一条订单。"""
        return (str(self.date), str(self.code), str(self.side))

    def to_dict(self) -> dict:
        d = {
            "date": self.date,
            "code": self.code,
            "name": self.name,
            "side": self.side,
            "order_type": self.order_type,
            "price": self.price,
            "quantity": self.quantity,
            "amount": self.amount,
            "decision": self.decision,
            "position_percent": self.position_percent,
            "stop_loss": self.stop_loss,
            "target_1": self.target_1,
            "risk_reward_1": self.risk_reward_1,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "notes": list(self.notes),
            "cost_pct": self.cost_pct,
            "cost_to_risk": self.cost_to_risk,
            "cost_warning": self.cost_warning,
            "generated_at": self.generated_at,
            "shadow": True,
        }
        return d


# --------------------------------------------------------------------------- #
# 5 道风控闸门（纯函数）
# --------------------------------------------------------------------------- #
def guard_connection(
    quote_ts: Optional[float],
    *,
    now: Optional[float] = None,
    cfg: Optional[RiskGuardConfig] = None,
) -> tuple[bool, str]:
    """闸门 4 · 断线即停：行情时间戳缺失或过期 → 一律不下单。

    为什么是「即停」而不是「用旧价继续」：执行层最危险的失效就是**拿一个过期的价格
    去下单** —— 你以为在 10.00 买，实际成交在 10.50。宁可今天不下单。
    """
    c = cfg or RiskGuardConfig()
    if quote_ts is None:
        return False, "无行情时间戳（视为断线）"
    try:
        ts = float(quote_ts)
    except (TypeError, ValueError):
        return False, "行情时间戳无法解析（视为断线）"
    ref = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    age = ref - ts
    if age > float(c.max_quote_staleness_sec):
        return False, (f"行情已过期 {age:.0f}s（上限 {c.max_quote_staleness_sec}s）→ 断线即停")
    return True, ""


def guard_daily_loss(
    equity_now: Optional[float],
    equity_prev_close: Optional[float],
    *,
    cfg: Optional[RiskGuardConfig] = None,
) -> tuple[bool, str]:
    """闸门 1 · 单日最大亏损上限：当日亏损超限 → 停止当日**所有**新开仓。

    注意是「停止新开仓」而非「强制平仓」：熔断的目的是**不让今天继续扩大风险**，
    不是制造一次恐慌性抛售。平仓由止损与卖出逻辑各自负责。
    """
    c = cfg or RiskGuardConfig()
    if equity_now is None or equity_prev_close is None:
        return True, ""                       # 没有基准就不误伤（保守不等于乱杀）
    if not (float(equity_prev_close) > 0):
        return True, ""
    ret = float(equity_now) / float(equity_prev_close) - 1.0
    if ret <= -abs(float(c.max_daily_loss_pct)):
        return False, (f"当日亏损 {ret:.2%} 已触及上限 "
                       f"{c.max_daily_loss_pct:.2%} → 停止当日新开仓")
    return True, ""


def guard_single_position(
    order_amount: float,
    held_amount: float,
    equity: Optional[float],
    *,
    cfg: Optional[RiskGuardConfig] = None,
) -> tuple[bool, str]:
    """闸门 2 · 单票上限：``已持有 + 本次委托`` 不得超过总资产的一定比例。

    必须算**已持有**部分：只校验单笔金额会漏掉「同一只票反复加仓把仓位堆上去」。
    """
    c = cfg or RiskGuardConfig()
    if equity is None or float(equity) <= 0:
        return False, "无总资产 → 无法校验单票上限"
    cap = float(equity) * float(c.max_single_pct)
    total = float(held_amount or 0.0) + float(order_amount or 0.0)
    if total > cap + 1e-9:
        return False, (f"单票合计 {total:,.0f} 元 > 上限 {cap:,.0f} 元"
                       f"（总资产 {c.max_single_pct:.0%}）")
    return True, ""


def guard_total_position(
    total_held_amount: float,
    order_amount: float,
    equity: Optional[float],
    *,
    cfg: Optional[RiskGuardConfig] = None,
) -> tuple[bool, str]:
    """闸门 3 · 总仓位上限：``全部持仓 + 本次委托`` 不得超过总资产的一定比例。"""
    c = cfg or RiskGuardConfig()
    if equity is None or float(equity) <= 0:
        return False, "无总资产 → 无法校验总仓位上限"
    cap = float(equity) * float(c.max_total_pct)
    total = float(total_held_amount or 0.0) + float(order_amount or 0.0)
    if total > cap + 1e-9:
        return False, (f"总仓位 {total:,.0f} 元 > 上限 {cap:,.0f} 元"
                       f"（总资产 {c.max_total_pct:.0%}）")
    return True, ""


def guard_duplicate(
    key: tuple[str, str, str],
    existing_keys: Iterable[tuple[str, str, str]],
) -> tuple[bool, str]:
    """闸门 5 · 幂等防重复下单：同一 (日期, 代码, 方向) 只能有一条订单。

    调度器一个交易日会跑多轮（慢轨 30~60 分钟一轮）。没有这道闸门，
    同一笔交易会被重复委托 —— 在真实账户里就是**仓位翻倍**。
    """
    k = (str(key[0]), str(key[1]), str(key[2]))
    if k in {tuple(str(x) for x in e) for e in existing_keys}:
        return False, f"重复下单：{k[0]} {k[1]} {k[2]} 已存在，跳过"
    return True, ""


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get(obj, key, default=None):
    """同时兼容 dict 与 dataclass/对象。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def shadow_orders_path(root: Optional[Path] = None) -> Path:
    return (Path(root) if root else _RESULTS) / "shadow_orders.jsonl"


def load_shadow_orders(root: Optional[Path] = None) -> list[dict]:
    """读取台账（跳过损坏行，与 ``paper_tracking`` 同策略）。"""
    path = shadow_orders_path(root)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _append_jsonl(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _cost_of(amount: Optional[float], entry: Optional[float],
             stop: Optional[float], market: str) -> tuple[Optional[float], Optional[float], bool]:
    """算双边成本（%）与「成本 / 止损距离」。成本是信息，不是闸门。"""
    from .backtest.execution import cost_model_for_market

    if not amount or not entry or entry <= 0:
        return None, None, False
    cm = cost_model_for_market(market)
    exit_px = stop if (stop and stop > 0) else entry
    cost_pct = float(cm.round_trip_cost_pct(entry, exit_px, notional=float(amount)))
    risk_pct = ((entry - stop) / entry * 100.0) if (stop and 0 < stop < entry) else None
    ratio = (cost_pct / risk_pct) if (risk_pct and risk_pct > 0) else None
    warn = bool(ratio is not None and ratio >= COST_TO_RISK_WARN)
    return round(cost_pct, 4), (round(ratio, 4) if ratio is not None else None), warn


def load_equity_baseline(root: Optional[Path] = None) -> tuple[Optional[float], Optional[float]]:
    """从净值历史取 ``(最新净值, 上一交易日净值)``，供单日亏损闸门使用。"""
    from .portfolio_risk import equity_values, load_equity_history

    vals = [v for v in equity_values(load_equity_history(root)) if v is not None]
    if not vals:
        return None, None
    if len(vals) == 1:
        return float(vals[-1]), None
    return float(vals[-1]), float(vals[-2])


def affordable_price_ceiling(
    equity: Optional[float],
    *,
    max_single_pct: float = 0.20,
    lot_size: int = 100,
) -> Optional[float]:
    """本账户**买得起**的最高股价（元/股）。

    A 股一手 100 股，所以「单票上限金额」除以 100 就是能承受的最高股价。
    高于它 → 一手就超过单票上限 → 该票在本账户上**完全不可交易**（不是仓位小，是 0 股）。

    这个数字比一句「仓位建议为 0」有用得多：它把「为什么这么多信号都执行不了」
    归结成一个可以拿去和信号价比大小的阈值。
    """
    if equity is None or float(equity) <= 0 or int(lot_size) <= 0:
        return None
    return round(float(equity) * float(max_single_pct) / int(lot_size), 2)


# --------------------------------------------------------------------------- #
# 影子下单主流程
# --------------------------------------------------------------------------- #
@dataclass
class ShadowRun:
    """一轮影子下单的结果。"""

    date: str = ""
    equity: Optional[float] = None
    orders: list[OrderIntent] = field(default_factory=list)
    guard_notes: list[str] = field(default_factory=list)

    def ready(self) -> list[OrderIntent]:
        return [o for o in self.orders if o.status == "READY"]

    def blocked(self) -> list[OrderIntent]:
        return [o for o in self.orders if o.status == "BLOCKED"]

    def skipped(self) -> list[OrderIntent]:
        return [o for o in self.orders if o.status == "SKIPPED"]

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "equity": self.equity,
            "n_ready": len(self.ready()),
            "n_blocked": len(self.blocked()),
            "n_skipped": len(self.skipped()),
            "guard_notes": list(self.guard_notes),
            "orders": [o.to_dict() for o in self.orders],
        }

    def summary(self) -> str:
        head = (f"影子模式 {self.date}：可下单 {len(self.ready())} / "
                f"被闸门拦下 {len(self.blocked())} / 本就不该下单 {len(self.skipped())}")
        if self.equity:
            head += f"（总资产 {self.equity:,.0f} 元）"
        lines = [head]
        if self.equity:
            ceil = affordable_price_ceiling(self.equity)
            if ceil is not None:
                lines.append(f"  本账户买得起的最高股价：{ceil:.2f} 元"
                             f"（一手 {RiskGuardConfig().lot_size} 股 × 单票上限 "
                             f"{RiskGuardConfig().max_single_pct:.0%}）")
        for n in self.guard_notes:
            lines.append(f"  闸门：{n}")
        for o in self.orders:
            tag = {"READY": "可下单", "BLOCKED": "已拦截", "SKIPPED": "跳过"}.get(o.status, o.status)
            extra = f" · {o.blocked_by}" if o.blocked_by else ""
            lines.append(f"  [{tag}] {o.code} {o.name} {o.side} "
                         f"{o.quantity}股 @ {o.price}{extra}")
        return "\n".join(lines)


def build_shadow_orders(
    plans: Iterable[Any],
    *,
    equity: Optional[float],
    holdings_value: float = 0.0,
    held_by_code: Optional[dict[str, float]] = None,
    guard_cfg: Optional[RiskGuardConfig] = None,
    equity_prev_close: Optional[float] = None,
    quote_ts: Optional[float] = None,
    now: Optional[float] = None,
    existing_keys: Optional[Iterable[tuple[str, str, str]]] = None,
    date: Optional[str] = None,
    market: str = "CN",
    risk_percent: float = 0.02,
    max_position_pct: Optional[float] = None,
) -> ShadowRun:
    """把交易计划转成订单意图，并逐条过 5 道闸门。**不下单。**

    Args:
        plans: 交易计划（dict 或 ``TradingPlan``）。
        equity: 账户总资产。为 None 时所有候选都会被跳过（无法定仓位）。
        holdings_value: 当前**全部持仓**市值（总仓位闸门用）。
        held_by_code: ``{code: 已持有市值}``（单票闸门用）。
        guard_cfg: 闸门参数。
        equity_prev_close: 上一交易日净值（单日亏损闸门用）。
        quote_ts: 最新行情时间戳（epoch 秒）；``None`` 视为断线。
        now: 当前时间（epoch 秒），默认取系统时间；便于测试注入。
        existing_keys: 台账里已存在的订单键（幂等闸门用）。
        date: 交易日（北京时间），默认取当日。
        market: 市场（成本模型）。
        risk_percent / max_position_pct: 传给 ``calc_position_size`` 的仓位参数。
    """
    from .opportunity import calc_position_size
    from .portfolio_risk import beijing_date

    cfg = guard_cfg or RiskGuardConfig()
    d = str(date or beijing_date())
    held_map = {str(k): float(v or 0.0) for k, v in (held_by_code or {}).items()}
    keys = list(existing_keys or [])

    run = ShadowRun(date=d, equity=equity)

    # ---- 全局闸门：这两条不过，当天一条订单都不生成 ----
    ok_conn, why_conn = guard_connection(quote_ts, now=now, cfg=cfg)
    if not ok_conn:
        run.guard_notes.append(why_conn)
    ok_daily, why_daily = guard_daily_loss(equity, equity_prev_close, cfg=cfg)
    if not ok_daily:
        run.guard_notes.append(why_daily)

    running_total = float(holdings_value or 0.0)

    for p in plans or []:
        # DecisionState 是 (str, Enum) 子类：str(枚举) 会得到 "DecisionState.BUY_NOW"，
        # 必须走 .value，否则所有计划都会被判成「不是可执行决策」而静默漏掉。
        raw_decision = _get(p, "decision")
        decision = str(getattr(raw_decision, "value", raw_decision) or "")
        code = str(_get(p, "code") or "").strip()
        if decision not in ACTIONABLE_DECISIONS or not code:
            continue

        name = str(_get(p, "name") or "")
        entry = _num(_get(p, "entry_price")) or _num(_get(p, "entry_high"))
        stop = _num(_get(p, "stop_loss"))
        t1 = _num(_get(p, "target_1"))
        rr = _num(_get(p, "risk_reward_1"))
        intent = OrderIntent(
            code=code, name=name, side="BUY", order_type="LIMIT",
            price=entry, decision=decision, stop_loss=stop, target_1=t1,
            risk_reward_1=rr, date=d, generated_at=_now_iso(),
        )

        # ---- 先定仓位（没有仓位就无从校验闸门）----
        if equity is None or equity <= 0:
            intent.status, intent.blocked_by = "SKIPPED", "无总资产，无法计算仓位"
            run.orders.append(intent)
            continue
        sizing = calc_position_size(
            float(equity), float(entry or 0.0), float(stop or 0.0),
            risk_percent=risk_percent,
            max_position_pct=float(max_position_pct if max_position_pct is not None
                                   else cfg.max_single_pct),
            lot_size=cfg.lot_size,
        )
        qty = int(sizing.suggested_shares or 0)
        amount = float(sizing.position_amount or 0.0)
        intent.quantity, intent.amount = qty, (amount or None)
        intent.position_percent = sizing.position_percent
        if qty <= 0 or amount <= 0:
            intent.status = "SKIPPED"
            intent.blocked_by = "仓位建议为 0（一手即超单票上限，或风险预算不足）"
            run.orders.append(intent)
            continue

        # ---- 成本（信息性）----
        cp, cr, cw = _cost_of(amount, entry, stop, market)
        intent.cost_pct, intent.cost_to_risk, intent.cost_warning = cp, cr, cw
        if cw:
            intent.notes.append(
                f"成本警告：双边 {cp}% ≈ 止损距离的 {cr:.0%}（门槛 {COST_TO_RISK_WARN:.0%}）")

        # ---- 5 道闸门 ----
        checks = [
            ("connection", (ok_conn, why_conn)),
            ("daily_loss", (ok_daily, why_daily)),
            ("duplicate", guard_duplicate((d, code, "BUY"), keys)),
            ("single_position", guard_single_position(
                amount, held_map.get(code, 0.0), equity, cfg=cfg)),
            ("total_position", guard_total_position(
                running_total, amount, equity, cfg=cfg)),
        ]
        blocked = next(((n, w) for n, (ok, w) in checks if not ok), None)
        if blocked:
            intent.status, intent.blocked_by = "BLOCKED", f"[{blocked[0]}] {blocked[1]}"
        else:
            intent.status = "READY"
            keys.append((d, code, "BUY"))      # 同轮内也要去重
            running_total += amount            # 逐笔累加，避免同轮多单合计超限

        run.orders.append(intent)

    return run


def record_shadow_orders(orders: Iterable[Any], *, root: Optional[Path] = None) -> list[dict]:
    """把订单意图追加到台账（只记 READY/BLOCKED/SKIPPED 中**非重复**的）。

    幂等由 ``build_shadow_orders`` 的 ``duplicate`` 闸门保证；这里再按 ``(日期, 代码, 方向)``
    对台账做一次去重，防止两次调用把同一笔写两遍。
    """
    rows = [o.to_dict() if hasattr(o, "to_dict") else dict(o) for o in orders or []]
    existing = {(str(r.get("date")), str(r.get("code")), str(r.get("side")))
                for r in load_shadow_orders(root)}
    fresh, seen = [], set()
    for r in rows:
        k = (str(r.get("date")), str(r.get("code")), str(r.get("side")))
        if k in existing or k in seen:
            continue
        seen.add(k)
        fresh.append(r)
    _append_jsonl(shadow_orders_path(root), fresh)
    return fresh


def recordable(run: "ShadowRun") -> list[OrderIntent]:
    """挑出**可以写进台账**的订单意图 —— 排除被「瞬时闸门」拦下的那些。

    为什么必须排除：台账是 append-only 且按 ``(日期, 代码, 方向)`` 幂等。
    如果一次「断线」或「当日亏损」把某只票的键占住，稍后恢复时这笔单就再也写不进去，
    对账会**永远报「漏单」**—— 一个假警报，而且会把真问题淹没。
    瞬时拦截只进日志与 ``guard_notes``，不进台账。
    """
    out: list[OrderIntent] = []
    for o in run.orders:
        if o.status == "BLOCKED" and any(f"[{g}]" in o.blocked_by for g in TRANSIENT_GUARDS):
            continue
        out.append(o)
    return out


# --------------------------------------------------------------------------- #
# 对账：执行层 vs 信号层
# --------------------------------------------------------------------------- #
def reconcile_with_signals(
    orders: Iterable[Any],
    signals: Iterable[dict],
    *,
    date: Optional[str] = None,
    price_tol: float = 0.001,
) -> dict:
    """校验「执行层要下的单」与「信号层的可执行信号」完全一致。

    这是前置条件 6 里「与人工预期 100% 对账」的**可自动化部分**：人工看信号、
    执行层看订单，两边本应是同一批代码、同一批价格。

    三类差异要**分开报**，因为它们是完全不同的问题：

    * ``missing_orders``  —— 有信号，执行层**连一条意图都没产生** → 真·漏单（bug）
    * ``infeasible``      —— 有信号、执行层也考虑了，但因账户规模/风控**明确拒绝**
      （``SKIPPED`` / ``BLOCKED``）。这**不是 bug**，是「信号层说能买、执行层说买不起」的
      结构性矛盾，必须单独暴露 —— 把它混进「漏单」会让真 bug 被噪声淹没。
    * ``extra_orders``    —— 没有信号却生成了可执行订单 → 凭空下单（比漏单更危险）

    返回 ``{"ok": bool, ...}``。``ok`` 只要求「没有漏单 / 没有凭空下单 / 价格口径一致」，
    ``infeasible`` 不影响 ``ok``（它是预期内的拦截，不是错误）。
    """
    def _d(x):
        return str(_get(x, "date") or _get(x, "signal_date") or "")

    sig = [s for s in (signals or []) if (not date or _d(s) == str(date))]
    ords = [o for o in (orders or []) if (not date or _d(o) == str(date))]

    status_by_code = {str(_get(o, "code") or ""): str(_get(o, "status") or "READY")
                      for o in ords}
    ready = [o for o in ords if str(_get(o, "status") or "READY") == "READY"]

    sig_map = {str(_get(s, "code") or ""): _num(_get(s, "entry_price")) for s in sig}
    ord_map = {str(_get(o, "code") or ""): _num(_get(o, "price")) for o in ready}

    missing = sorted(set(sig_map) - set(status_by_code))
    extra = sorted(set(ord_map) - set(sig_map))
    infeasible = {c: status_by_code[c] for c in sorted(set(sig_map) & set(status_by_code))
                  if status_by_code[c] != "READY"}

    mismatch = []
    for c in sorted(set(sig_map) & set(ord_map)):
        a, b = sig_map[c], ord_map[c]
        if a is None or b is None:
            continue
        if abs(a - b) > price_tol * max(1.0, abs(a)):
            mismatch.append({"code": c, "signal_price": a, "order_price": b})

    return {
        "ok": not (missing or extra or mismatch),
        "n_signals": len(sig),
        "n_orders": len(ready),
        "n_infeasible": len(infeasible),
        "missing_orders": missing,     # 有信号但执行层毫无反应 → 真·漏单
        "infeasible": infeasible,      # 有信号但被明确拒绝（账户规模/风控）→ 结构性矛盾
        "extra_orders": extra,         # 没信号却生成了订单 → 执行层凭空下单
        "price_mismatch": mismatch,    # 价格口径分叉
    }


def summarize_shadow(root: Optional[Path] = None) -> dict:
    """台账汇总：各状态计数 + 成本警告数（供看板/邮件/周报复用）。"""
    rows = load_shadow_orders(root)
    by_status: dict[str, int] = {}
    for r in rows:
        s = str(r.get("status") or "UNKNOWN")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "n_total": len(rows),
        "by_status": by_status,
        "n_cost_warning": sum(1 for r in rows if r.get("cost_warning")),
        "dates": sorted({str(r.get("date") or "") for r in rows if r.get("date")}),
        "last_generated_at": (rows[-1].get("generated_at") if rows else None),
    }
