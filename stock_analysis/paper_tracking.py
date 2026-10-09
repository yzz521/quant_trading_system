"""纸面跟踪：把系统信号变成一份不花钱、也无法事后修改的成绩单。

为什么需要
----------
「系统信号到底赚不赚钱」目前只能靠回测回答，而回测的成交假设可以自己骗自己
（见 ``backtest`` 模块的成交真实性五件套）。纸面跟踪把「信号发出之后的真实走势」
原样记下来，是唯一既不需要真金白银、又能用真实行情检验的路径。

设计
----
* **两个 append-only JSONL**，写入后不再修改，天然可审计：
    - ``paper_signals.jsonl``     —— 每个信号一行：T 日系统说了什么（不可改）
    - ``paper_settlements.jsonl`` —— 每个已了结信号一行：真实走势给出的结果
  之所以不用「一个文件原地更新状态」，是因为那样无法证明「事后没改过结论」。
* **结算复用回测的 ``TradingPlanBacktest._simulate``**，规则与回测完全一致
  （成本 / 涨跌停 / 停牌 / 跳空 / 容量），避免「回测一套、跟踪另一套」。
* 只跟踪 ``BUY_NOW`` / ``BUY_ON_PULLBACK`` —— WATCH / AVOID 不是可执行建议，
  把它们算进成绩单会稀释掉真正该被检验的东西。
* 未成交（价格从未触及委托价）**也要记录**：它是「信号看起来能买、实际买不到」
  的证据，正是回测最容易高估收益的地方。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from ..utils import get_logger

log = get_logger("PaperTracking")

#: 值得跟踪的决策（可执行建议）。WATCH/AVOID 不计入。
TRACKED_DECISIONS = ("BUY_NOW", "BUY_ON_PULLBACK")
DEFAULT_MAX_HOLD_DAYS = 60
#: 单次结算最多处理多少个未了结信号（防止一次拉几百只行情把接口打爆）
MAX_SETTLE_PER_RUN = 60

_RESULTS = Path(__file__).resolve().parents[1] / "results"


# --------------------------------------------------------------------------- #
# 路径与读写
# --------------------------------------------------------------------------- #
def signals_path(root: Optional[Path] = None) -> Path:
    return (Path(root) if root else _RESULTS) / "paper_signals.jsonl"


def settlements_path(root: Optional[Path] = None) -> Path:
    return (Path(root) if root else _RESULTS) / "paper_settlements.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
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
            continue          # 半行/损坏行直接跳过，不让一行坏数据毁掉整份成绩单
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


def load_signals(root: Optional[Path] = None) -> list[dict]:
    return _read_jsonl(signals_path(root))


def load_settlements(root: Optional[Path] = None) -> list[dict]:
    return _read_jsonl(settlements_path(root))


# --------------------------------------------------------------------------- #
# 记录信号
# --------------------------------------------------------------------------- #
def _signal_key(rec: dict) -> tuple[str, str]:
    return (str(rec.get("signal_date") or ""), str(rec.get("code") or ""))


def record_signals(
    plans: Iterable[Any],
    *,
    date: Optional[str] = None,
    root: Optional[Path] = None,
    decisions: Sequence[str] = TRACKED_DECISIONS,
) -> list[dict]:
    """把当日的可执行信号落盘（幂等：同一 ``(日期, 代码)`` 只留一条）。

    返回本次**新写入**的记录。同一天调度器会跑多轮，幂等是必须的，否则
    一笔交易会被记成好几笔、胜率统计直接失真。
    """
    d = str(date or _beijing_date())
    existing = {_signal_key(r) for r in load_signals(root)}
    fresh: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for p in plans or []:
        get = p.get if isinstance(p, dict) else (lambda k, _p=p: getattr(_p, k, None))
        if str(get("decision") or "") not in decisions:
            continue
        code = str(get("code") or "").strip()
        if not code:
            continue
        key = (d, code)
        if key in existing or key in seen:
            continue
        seen.add(key)
        fresh.append({
            "signal_date": d,
            "code": code,
            "name": str(get("name") or ""),
            "decision": str(get("decision") or ""),
            "entry_price": _num(get("entry_price")),
            "entry_low": _num(get("entry_low")),
            "entry_high": _num(get("entry_high")),
            "stop_loss": _num(get("stop_loss")),
            "target_1": _num(get("target_1")),
            "target_2": _num(get("target_2")),
            "risk_reward_1": _num(get("risk_reward_1")),
            "confidence": _num(get("confidence")),
            "stock_score": _num(get("stock_score")),
            "opportunity_score": _num(get("opportunity_score")),
            "recorded_at": _now_iso(),
        })
    _append_jsonl(signals_path(root), fresh)
    return fresh


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _beijing_date() -> str:
    from .portfolio_risk import beijing_date

    return str(beijing_date())


# --------------------------------------------------------------------------- #
# 未了结信号
# --------------------------------------------------------------------------- #
def settled_keys(settlements: Sequence[dict]) -> set[tuple[str, str]]:
    return {(str(s.get("signal_date") or ""), str(s.get("code") or ""))
            for s in settlements}


def open_signals(
    *,
    root: Optional[Path] = None,
    signals: Optional[Sequence[dict]] = None,
    settlements: Optional[Sequence[dict]] = None,
) -> list[dict]:
    """尚未了结的信号（按信号日升序）。"""
    sigs = list(signals if signals is not None else load_signals(root))
    done = settled_keys(settlements if settlements is not None else load_settlements(root))
    out = [s for s in sigs if _signal_key(s) not in done]
    out.sort(key=lambda s: (str(s.get("signal_date") or ""), str(s.get("code") or "")))
    return out


# --------------------------------------------------------------------------- #
# 结算
# --------------------------------------------------------------------------- #
def default_loader(code: str, days: int = 500):
    """默认行情加载器：拉日K 并加指标（与回测同口径）。"""
    from .data_fetcher import detect_market, fetch_kline
    from .indicators import add_all_indicators

    df = fetch_kline(detect_market(code), days=days)
    if df is None or len(df) == 0:
        return None
    return add_all_indicators(df)


def settle_signals(
    *,
    loader: Optional[Callable[..., Any]] = None,
    root: Optional[Path] = None,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    market: str = "CN",
    limit: int = MAX_SETTLE_PER_RUN,
) -> list[dict]:
    """对未了结信号跑真实走势，把已了结的写进 ``paper_settlements.jsonl``。

    结算规则**直接复用** ``TradingPlanBacktest._simulate`` —— 回测与跟踪必须
    用同一套成交/成本/涨跌停假设，否则跟踪结果无法用来检验回测结论。

    仍可能继续走的信号（持有期未满且未触发止损/目标）保持未了结，下次再算。
    """
    from .backtest.execution import ExecutionConfig, apply_market, limit_pct_for_code
    from .backtest.trading_plan_backtest import BacktestTrade, TradingPlanBacktest

    load = loader or default_loader
    pending = open_signals(root=root)
    if limit and limit > 0:
        pending = pending[:limit]

    cfg = apply_market(ExecutionConfig(), market)
    sim = TradingPlanBacktest(exec_config=cfg, market=market, max_hold_days=max_hold_days)
    settled: list[dict] = []

    for sig in pending:
        code = str(sig.get("code") or "")
        sig_date = str(sig.get("signal_date") or "")
        try:
            df = load(code)
        except Exception as e:  # noqa: BLE001
            log.debug("纸面结算取数失败 %s: %s", code, e)
            continue
        if df is None or len(df) == 0:
            continue

        try:
            rec = _settle_one(sig, df, sim=sim, cfg=cfg, market=market,
                              max_hold_days=max_hold_days,
                              limit_pct=limit_pct_for_code(code),
                              trade_cls=BacktestTrade)
        except Exception as e:  # noqa: BLE001
            # 单只票的数据异常不能带走整批：`_append_jsonl` 在循环之后，
            # 一次未捕获的异常会让本轮所有已算出的结算结果全部丢失。
            log.warning("纸面结算失败 %s: %s", code, e)
            continue
        if rec is not None:
            settled.append(rec)
            # 逐个落盘而非攒到循环末尾一次性写：进程中途被 kill（或被
            # 上一层的超时打断）时，已算出的结算结果不该跟着蒸发。
            # 追加写 + `_read_jsonl` 跳过损坏行，最坏只丢最后半行。
            _append_jsonl(settlements_path(root), [rec])

    if settled:
        log.info("纸面结算完成：新增 %d 笔", len(settled))
    return settled


def _settle_one(sig: dict, df, *, sim, cfg, market, max_hold_days,
                limit_pct, trade_cls) -> Optional[dict]:
    """对一个信号跑完真实走势，返回结算记录；还不到时候就返回 None。"""
    import pandas as pd

    sig_date = pd.Timestamp(str(sig.get("signal_date"))).normalize()
    # 统一成 DatetimeIndex：真实行情（fetch_kline → add_all_indicators）的
    # index 本身就是 DatetimeIndex 且**没有 date 列**，而 pd.to_datetime 作用在
    # DatetimeIndex 上返回的仍是 DatetimeIndex（没有 .iloc）——早先按 Series 写，
    # 真实数据一到就 AttributeError，测试用带 date 列的假 df 才没暴露。
    raw_dates = df["date"] if "date" in df.columns else df.index
    dates = pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="coerce"))
    if len(dates) == 0:
        return None
    d = df.reset_index(drop=True)
    # NaT 不能用 `is not None` 判掉（NaT is not None 为真），必须用 pd.isna
    pos = [i for i, ts in enumerate(dates)
           if not pd.isna(ts) and ts.normalize() >= sig_date]
    if not pos:
        return None                      # 信号日之后还没有行情，无法开始
    i = pos[0]

    future = d.iloc[i + 1 : i + 1 + max_hold_days]
    if future.empty:
        return None                      # 次日还没到，等下次
    closes = pd.to_numeric(d["close"], errors="coerce").tolist()
    trade = trade_cls(
        date=str(dates[i].date()),
        code=str(sig.get("code") or ""),
        name=str(sig.get("name") or ""),
        decision=str(sig.get("decision") or ""),
        entry_low=float(sig.get("entry_low") or sig.get("entry_price") or 0.0),
        entry_price=float(sig.get("entry_price") or 0.0),
        entry_high=float(sig.get("entry_high") or 0.0),
        stop_loss=float(sig.get("stop_loss") or 0.0),
        target_1=float(sig.get("target_1") or 0.0),
        target_2=float(sig.get("target_2") or 0.0),
        confidence=sig.get("confidence"),
        opportunity_score=sig.get("opportunity_score"),
        stock_score=sig.get("stock_score"),
        risk_reward_1=sig.get("risk_reward_1"),
    )
    ctx = {
        "config": cfg,
        "limit_pct": limit_pct,
        "prev_closes": closes[i : i + len(future)],
        "future_dates": [str(t.date()) for t in dates[i + 1 : i + 1 + len(future)]],
        "base_bar": i,
        "plan_date": trade.date,
    }
    sim._simulate(trade, future, ctx)

    # 还没走完：持有期未满、且未触发止损/目标 → 保持未了结
    exhausted = len(future) >= max_hold_days
    if trade.exit_reason in ("", "not_entered") and not exhausted:
        return None

    return {
        "signal_date": str(sig.get("signal_date")),
        "code": trade.code,
        "name": trade.name,
        "decision": trade.decision,
        "entry_price": trade.entry_price,
        "stop_loss": trade.stop_loss,
        "target_1": trade.target_1,
        "entry_executed": bool(trade.entry_executed),
        "entry_exec_price": trade.entry_exec_price,
        "entry_date": trade.entry_date,
        "fill_type": trade.fill_type,
        "days_to_entry": trade.days_to_entry,
        "exit_reason": trade.exit_reason or ("timeout" if exhausted else ""),
        "exit_date": trade.exit_date,
        "holding_days": trade.holding_days,
        "return_pct": trade.return_pct,
        "gross_return_pct": trade.gross_return_pct,
        "cost_pct": trade.cost_pct,
        "gapped": bool(trade.gapped),
        "limit_blocked_days": trade.limit_blocked_days,
        "capacity_limited": bool(trade.capacity_limited),
        "risk_reward_1": trade.risk_reward_1,
        "confidence": trade.confidence,
        "settled_at": _now_iso(),
    }


# --------------------------------------------------------------------------- #
# 成绩单
# --------------------------------------------------------------------------- #
def summarize(
    *,
    root: Optional[Path] = None,
    signals: Optional[Sequence[dict]] = None,
    settlements: Optional[Sequence[dict]] = None,
) -> dict:
    """纸面跟踪成绩单。

    ``n_trades`` 只数**成交过**的完整交易（未成交的信号不计入胜率，但单独报出
    ``n_not_entered`` —— 它衡量「信号看起来能买、实际买不到」的比例）。
    """
    sigs = list(signals if signals is not None else load_signals(root))
    sets = list(settlements if settlements is not None else load_settlements(root))
    sig_dates = sorted({str(s.get("signal_date") or "") for s in sigs if s.get("signal_date")})

    executed = [s for s in sets if s.get("entry_executed")]
    rets = [float(s["return_pct"]) for s in executed if _num(s.get("return_pct")) is not None]

    out: dict[str, Any] = {
        "n_signals": len(sigs),
        "n_settled": len(sets),
        "n_open": max(0, len(sigs) - len(sets)),
        "n_trades": len(executed),
        "n_not_entered": len(sets) - len(executed),
        "signal_dates": len(sig_dates),
        "first_signal_date": sig_dates[0] if sig_dates else None,
        "last_signal_date": sig_dates[-1] if sig_dates else None,
    }
    if not rets:
        out.update({"win_rate": None, "avg_return_pct": None, "median_return_pct": None,
                    "total_return_pct": None, "max_drawdown_pct": None})
        return out

    import numpy as np

    arr = np.asarray(rets, dtype=float)
    # 按了结时间排序算回撤：只有按发生顺序累积，回撤才有意义
    ordered = sorted(executed, key=lambda s: (str(s.get("exit_date") or ""),
                                               str(s.get("signal_date") or "")))
    equity = np.cumprod(1.0 + np.asarray([float(s["return_pct"]) / 100.0 for s in ordered]))
    peak = np.maximum.accumulate(equity)
    dd = float(np.min(equity / peak - 1.0)) * 100.0 if len(equity) else 0.0

    out.update({
        "win_rate": float((arr > 0).mean()),
        "avg_return_pct": float(arr.mean()),
        "median_return_pct": float(np.median(arr)),
        "best_return_pct": float(arr.max()),
        "worst_return_pct": float(arr.min()),
        "total_return_pct": float((equity[-1] - 1.0) * 100.0),
        "max_drawdown_pct": dd,
    })
    return out
