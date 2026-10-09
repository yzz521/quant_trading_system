"""实时盯盘引擎（快轨）—— 与每日决策（慢轨）解耦。

为什么需要它
------------
``MarketScheduler`` 一轮要做「持仓盈亏 + 持仓动作 + 持仓量化 + 全市场初筛 +
批量机会扫描 + 拼邮件」，单轮分钟级，所以间隔只能 30~60 分钟 —— 它回答的是
「今天该关注哪些票」，不是「现在这一刻要不要动手」。

本模块只回答后者：拿一批票的**实时快照**过一遍规则，命中就推。几十只票用
1 个批量请求（``qt.gtimg.cn``，50 只/请求）即可覆盖，盘中 3~5 秒一轮毫无压力。

设计要点
--------
* **默认关闭**：``notify.yaml`` 中 ``realtime.enabled`` 为 false 时本模块不产生
  任何网络请求，系统行为与改动前完全一致。
* **纯快照规则**：只用批量行情快照 + 内存价格序列 + 持仓成本价，**不逐只拉 K 线**
  （K 线类指标留在慢轨，避免单轮扫描被拖慢）。
* **冷却去重**：同一只票的同一规则在 ``cooldown_min`` 内只推一次；全局限流
  ``min_push_gap_sec`` 防止刷屏，被限流的告警转入摘要缓冲而非丢弃。
* **分级推送**：致命级（止损 / 急杀 / 跌破关键位）立即推；其余信息级按
  ``digest_min`` 聚合成一条摘要，避免噪音淹没核心信号。
* **可观测**：每轮都往 ``realtime_state.json`` 写心跳（最近一轮时间 / 当日轮数 /
  取价只数 / 命中条数 / 最近推送），并在 ``app.log`` 里定期打一条心跳日志 ——
  "它到底有没有在跑"必须一眼能看出来，而不是靠等邮件反推。
* **失败降级**：行情取数失败只记日志，不推送、不中断循环。

命令行::

    python -m quant_trading_system.stock_analysis.realtime --status   # 在不在跑？跑到哪了？
    python -m quant_trading_system.stock_analysis.realtime --once     # 跑一轮看效果
    python -m quant_trading_system.stock_analysis.realtime --dry-run  # 只打印不推送
    python -m quant_trading_system.stock_analysis.realtime            # 常驻盯盘
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..utils import deep_merge, get_logger, load_yaml
from .data_fetcher import fetch_tencent_quotes
from .holdings import Holdings
from .notifier import Notifier

log = get_logger("Realtime")

try:
    from zoneinfo import ZoneInfo

    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    BEIJING = timezone(timedelta(hours=8))

# 告警级别：1=需立即处理（立即推） 2=提示 3=资讯（建议走摘要）
SEV_ACTION = 1
SEV_NOTICE = 2
SEV_INFO = 3

_SEV_TAG = {SEV_ACTION: "⚠️", SEV_NOTICE: "📌", SEV_INFO: "·"}
_SEV_LABEL = {SEV_ACTION: "立即处理", SEV_NOTICE: "提示", SEV_INFO: "资讯"}

# 价格序列保留的样本上限（5s 一轮 ≈ 20 分钟，足够覆盖分钟级急拉急杀窗口）
_HISTORY_CAP = 240


# --------------------------------------------------------------------------- #
# 交易时段 —— 与 ``MarketScheduler._in_session`` 同口径。
# 此处独立实现而非复用，是为了让快轨能单独跑（例如只跑盯盘、不起慢轨调度器），
# 两者之间不发生耦合。
# --------------------------------------------------------------------------- #
def in_session(market: str, now: Optional[datetime] = None, us_winter: bool = True) -> bool:
    """判断某市场当前是否在连续竞价时段（北京时间口径）。"""
    now = now or datetime.now(BEIJING)
    wd = now.weekday()  # Mon=0..Sun=6
    h = now.hour + now.minute / 60.0

    if market == "CN":
        return wd < 5 and ((9.5 <= h < 11.5) or (13.0 <= h < 15.0))
    if market == "HK":
        return wd < 5 and ((9.5 <= h < 12.0) or (13.0 <= h < 16.0))
    if market == "US":
        start, end = (21.5, 4.0) if us_winter else (22.5, 3.0)
        if wd < 5 and start <= h < 24:
            return True
        return 0 < wd <= 5 and 0 <= h < end
    return False


def is_etf(code: str) -> bool:
    """A 股 ETF / LOF 判定：51/56/58xxxx（沪）、15/16xxxx（深）。

    ETF 波动天然小于个股，涨跌幅阈值要单独放宽口径（默认 ±2% vs 个股 ±4%）。
    """
    c = str(code).strip()
    if not c.isdigit():
        return False
    return c.zfill(6).startswith(("51", "56", "58", "15", "16"))


# --------------------------------------------------------------------------- #
# 告警
# --------------------------------------------------------------------------- #
@dataclass
class Alert:
    code: str
    name: str
    rule: str
    severity: int
    title: str
    detail: str
    price: Optional[float] = None
    pct_chg: Optional[float] = None

    @property
    def key(self) -> str:
        """冷却去重键：同一只票 + 同一条规则。"""
        return f"{self.code}|{self.rule}"

    def line(self) -> str:
        px = f"现价 {self.price:g}" if self.price is not None else ""
        pct = f"{self.pct_chg:+.2f}%" if self.pct_chg is not None else ""
        head = " ".join(x for x in (self.name, f"({self.code})", px, pct) if x)
        return f"{head}\n    {self.title}：{self.detail}"


# --------------------------------------------------------------------------- #
# 规则引擎
# --------------------------------------------------------------------------- #
class RuleEngine:
    """纯快照规则引擎：输入一行行情，输出该行命中的告警列表。

    ``ctx`` 需要包含本轮上下文::

        {"market": "CN", "cost_price": 10.78, "prev_price": 10.5,
         "peak_price": 11.2, "history": [[ts, price], ...]}
    """

    def __init__(self, cfg: Optional[dict] = None) -> None:
        self.cfg: dict = cfg or {}

    # -------------------------------------------------------------- helpers
    def _rule(self, name: str) -> dict:
        return self.cfg.get(name) or {}

    @staticmethod
    def _f(d: dict, key: str, default: float) -> float:
        try:
            v = d.get(key)
            return default if v is None else float(v)
        except Exception:  # noqa: BLE001
            return default

    def pct_threshold(self, code: str, market: str) -> float:
        c = self._rule("pct_change")
        if market == "CN":
            key = "cn_etf" if is_etf(code) else "cn_stock"
            return self._f(c, key, 2.0 if key == "cn_etf" else 4.0)
        if market == "HK":
            return self._f(c, "hk", 3.0)
        return self._f(c, "us", 5.0)

    # -------------------------------------------------------------- evaluate
    def evaluate(self, row: dict[str, Any], ctx: dict[str, Any]) -> list[Alert]:
        code = str(row.get("code") or "")
        if not code:
            return []
        name = str(row.get("name") or code)
        market = str(ctx.get("market") or "")
        price = row.get("close")
        pct = row.get("pct_chg")
        if price is None:
            return []

        out: list[Alert] = []
        for fn in (
            self._r_pct_change,
            self._r_speed,
            self._r_stop_loss,
            self._r_take_profit,
            self._r_trailing_stop,
            self._r_price_cross,
            self._r_turnover,
        ):
            try:
                hit = fn(code, name, market, price, pct, row, ctx)
            except Exception as e:  # noqa: BLE001
                log.debug("规则 %s 计算失败 %s: %s", fn.__name__, code, e)
                hit = None
            if hit:
                out.append(hit)
        return out

    # -------------------------------------------------------------- 各条规则
    def _r_pct_change(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("pct_change")
        if not c.get("enabled", True) or pct is None:
            return None
        thr = self.pct_threshold(code, market)
        if abs(float(pct)) < thr:
            return None
        down = float(pct) < 0
        # 超过 1.5 倍阈值视为"异动加剧"，升级为需立即处理
        sev = SEV_ACTION if abs(float(pct)) >= thr * 1.5 else SEV_NOTICE
        return Alert(
            code=code, name=name, rule="pct_change", severity=sev,
            title=f"日内{'大跌' if down else '大涨'}异动",
            detail=(
                f"现价 {price:g} 日内{'跌' if down else '涨'} {abs(float(pct)):.2f}%，"
                f"已越过 {'ETF' if market == 'CN' and is_etf(code) else market} 阈值 ±{thr:g}%"
            ),
            price=price, pct_chg=pct,
        )

    def _r_speed(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("speed")
        if not c.get("enabled", True):
            return None
        window_min = self._f(c, "window_min", 3.0)
        thr = self._f(c, "pct", 1.0)
        hist = ctx.get("history") or []
        if len(hist) < 2:
            return None
        # 取"距现在至少 window 之前"的最近一个样本做对比；样本不足则不判
        cutoff = ctx["now_ts"] - window_min * 60 * 0.9
        base = None
        for ts, px in hist:
            if ts <= cutoff:
                base = px
            else:
                break
        if not base or price == base:
            return None
        move = (float(price) / float(base) - 1.0) * 100.0
        if abs(move) < thr:
            return None
        down = move < 0
        return Alert(
            code=code, name=name, rule="speed", severity=SEV_ACTION if down else SEV_NOTICE,
            title=f"{int(window_min)} 分钟{'急杀' if down else '急拉'}",
            detail=(
                f"{window_min:g} 分钟内从 {float(base):g} 到 {price:g}"
                f"（{move:+.2f}%），已越过 ±{thr:g}% 阈值"
            ),
            price=price, pct_chg=pct,
        )

    def _r_stop_loss(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("stop_loss")
        if not c.get("enabled", True):
            return None
        cost = ctx.get("cost_price")
        if not cost:
            return None
        thr = self._f(c, "pct", -8.0)  # 负数
        pnl = (float(price) / float(cost) - 1.0) * 100.0
        if pnl > thr:
            return None
        return Alert(
            code=code, name=name, rule="stop_loss", severity=SEV_ACTION,
            title="成本止损触发",
            detail=(
                f"现价 {price:g} 较成本 {float(cost):g} 为 {pnl:+.2f}%，"
                f"已跌破 {thr:g}% 止损线 —— 请人工确认是否执行减仓"
            ),
            price=price, pct_chg=pct,
        )

    def _r_take_profit(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("take_profit")
        if not c.get("enabled", True):
            return None
        cost = ctx.get("cost_price")
        if not cost:
            return None
        thr = self._f(c, "pct", 15.0)
        pnl = (float(price) / float(cost) - 1.0) * 100.0
        if pnl < thr:
            return None
        return Alert(
            code=code, name=name, rule="take_profit", severity=SEV_NOTICE,
            title="成本止盈触发",
            detail=(
                f"现价 {price:g} 较成本 {float(cost):g} 已达 {pnl:+.2f}%（阈值 +{thr:g}%）"
                " —— 到价不等于必卖，先看预期兑现度与是否有新增催化"
            ),
            price=price, pct_chg=pct,
        )

    def _r_trailing_stop(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("trailing_stop")
        if not c.get("enabled", False):
            return None
        cost = ctx.get("cost_price")
        peak = ctx.get("peak_price")
        if not cost or not peak:
            return None
        start_pct = self._f(c, "start_pct", 5.0)
        dd_pct = self._f(c, "drawdown_pct", 10.0)
        peak_pnl = (float(peak) / float(cost) - 1.0) * 100.0
        if peak_pnl < start_pct:  # 还没启动追踪
            return None
        dd = (float(peak) - float(price)) / float(peak) * 100.0
        if dd < dd_pct:
            return None
        return Alert(
            code=code, name=name, rule="trailing_stop", severity=SEV_ACTION,
            title="动态止盈（追踪止损）触发",
            detail=(
                f"峰值 {float(peak):g}（曾盈利 {peak_pnl:+.2f}%），"
                f"现价 {price:g} 自峰值回撤 {dd:.2f}%，已达 {dd_pct:g}% 回撤阈值"
            ),
            price=price, pct_chg=pct,
        )

    def _r_price_cross(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("price_cross")
        if not c.get("enabled", False):
            return None
        # 价位来源两级：慢轨自动生成的（止损位 / 卖出区间下沿）打底，手输价位覆盖它
        auto = (ctx.get("auto_levels") or {}).get(str(code)) or {}
        manual = (c.get("levels") or {}).get(str(code)) or {}
        merged = {**auto, **manual}
        above, below = merged.get("above"), merged.get("below")
        if above is None and below is None:
            return None
        above_label = merged.get("above_label") or "关键位"
        below_label = merged.get("below_label") or "关键位"

        prev = ctx.get("prev_price")
        if prev is None:
            # 首轮或重启后没有前值，无法判定"穿越"。默认只记录不报；
            # 开了 alert_if_outside 则对"本来就已经在关键位外侧"的补报一次
            # （否则像深套仓位永远等不到 a cross 事件，会被误认为功能失效）
            if not c.get("alert_if_outside", False):
                return None
            if below is not None and float(price) <= float(below):
                return Alert(
                    code=code, name=name, rule="price_cross_down", severity=SEV_ACTION,
                    title=f"已在{below_label}下方",
                    detail=f"现价 {price:g} 已低于 {float(below):g}（{below_label}），非新发生穿越，先确认一次",
                    price=price, pct_chg=pct,
                )
            if above is not None and float(price) >= float(above):
                return Alert(
                    code=code, name=name, rule="price_cross_up", severity=SEV_NOTICE,
                    title=f"已在{above_label}上方",
                    detail=f"现价 {price:g} 已高于 {float(above):g}（{above_label}），非新发生穿越，先确认一次",
                    price=price, pct_chg=pct,
                )
            return None

        if above is not None and float(prev) < float(above) <= float(price):
            return Alert(
                code=code, name=name, rule="price_cross_up", severity=SEV_NOTICE,
                title=f"上穿{above_label}",
                detail=f"现价 {price:g} 上穿 {float(above):g}（前值 {float(prev):g}）",
                price=price, pct_chg=pct,
            )
        if below is not None and float(prev) > float(below) >= float(price):
            return Alert(
                code=code, name=name, rule="price_cross_down", severity=SEV_ACTION,
                title=f"跌破{below_label}",
                detail=f"现价 {price:g} 跌破 {float(below):g}（前值 {float(prev):g}）",
                price=price, pct_chg=pct,
            )
        return None

    def _r_turnover(self, code, name, market, price, pct, row, ctx) -> Optional[Alert]:
        c = self._rule("turnover_surge")
        if not c.get("enabled", False):
            return None
        thr = self._f(c, "pct", 10.0)
        to = row.get("turnover")
        if to is None or float(to) < thr:
            return None
        return Alert(
            code=code, name=name, rule="turnover_surge", severity=SEV_INFO,
            title="换手率异动",
            detail=f"换手率 {float(to):.2f}% 已达 {thr:g}% 阈值（放量信号）",
            price=price, pct_chg=pct,
        )


# --------------------------------------------------------------------------- #
# 盯盘主循环
# --------------------------------------------------------------------------- #
class RealtimeWatcher:
    """轻量快轨盯盘：批量快照 → 规则判断 → 冷却去重 → 分级推送。"""

    def __init__(
        self,
        config_path: str = "config/notify.yaml",
        *,
        quote_fetcher: Optional[Callable[[list[str]], Any]] = None,
        notifier: Optional[Any] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        dry_run: bool = False,
        stop_event: Optional[threading.Event] = None,
        overrides: Optional[dict] = None,
    ) -> None:
        self.config_path = str(config_path)
        self.quote_fetcher = quote_fetcher or fetch_tencent_quotes
        self._notifier_override = notifier
        self._now_fn = now_fn or (lambda: datetime.now(BEIJING))
        self.dry_run = dry_run
        # 内存覆盖（深合并到磁盘配置之上），供配置页"试跑"用：
        # 让试跑验证的是**屏幕上当前的设置**，而不是还没保存的旧配置
        self._overrides = deepcopy(overrides) if overrides else {}
        # 允许外部（如桌面应用的后台线程）注入同一个停止事件，stop() 即可打断 sleep
        self._stop = stop_event or threading.Event()
        self._buffer: list[Alert] = []
        self._last_push = 0.0
        self._last_digest = 0.0
        # 最近一轮的统计快照，供 run_forever 打心跳日志 / 调用方自省
        self.last_stats: dict[str, Any] = {}
        self._notify_snapshot: Any = object()
        self.notifier: Optional[Any] = None
        self.reload()

    # ------------------------------------------------------------ 配置
    def reload(self) -> None:
        """重读 notify.yaml，配置页保存后下一轮即可生效。"""
        self.cfg = load_yaml(self.config_path) or {}
        if self._overrides:
            deep_merge(self.cfg, deepcopy(self._overrides))
        rt = self.cfg.get("realtime") or {}
        self.enabled = bool(rt.get("enabled", False))
        self.interval = max(1, int(rt.get("interval_sec", 5)))
        self.idle_interval = max(1, int(rt.get("idle_interval_sec", 60)))
        self.watch_mode = str(rt.get("watch_mode", "holdings"))
        self.cooldown = float(rt.get("cooldown_min", 30)) * 60
        self.min_push_gap = float(rt.get("min_push_gap_sec", 20))
        self.digest = float(rt.get("digest_min", 0)) * 60
        self.watchlist_cfg = rt.get("watchlist") or []
        self.levels_from_cache = bool(rt.get("levels_from_cache", False))
        self.us_winter = bool((self.cfg.get("schedule") or {}).get("us_winter", True))

        rules = dict(rt.get("rules") or {})
        pc = dict(rules.get("price_cross") or {})
        # 没显式写 price_cross.enabled 时跟着 levels_from_cache 走：开了自动取价位
        # 却还要再手动打开一次开关，是很容易踩空的坑
        if "enabled" not in pc:
            pc["enabled"] = self.levels_from_cache
        rules["price_cross"] = pc
        self.engine = RuleEngine(rules)

        notify = self.cfg.get("notify")
        if self._notifier_override is not None:
            self.notifier = self._notifier_override
        elif notify != self._notify_snapshot:
            try:
                self.notifier = Notifier(self.config_path)
            except Exception as e:  # noqa: BLE001
                log.warning("通知渠道初始化失败: %s", e)
                self.notifier = None
            self._notify_snapshot = notify

    # ------------------------------------------------------------ 盯盘清单
    def watchlist(self) -> list[dict]:
        """返回本次要盯的票 ``[{code, name, market, cost_price, quantity}]``。

        持仓优先（带成本价 → 可跑止损/止盈规则），``watchlist`` 补充自选票。
        """
        items: dict[str, dict] = {}
        if self.watch_mode in ("holdings", "both"):
            hpath = str(Path(self.config_path).parent / "holdings.yaml")
            try:
                for h in Holdings(hpath).all():
                    code = str(h.get("code") or "").strip()
                    if not code:
                        continue
                    items[code] = {
                        "code": code,
                        "name": h.get("name") or code,
                        "market": str(h.get("market") or "CN"),
                        "cost_price": h.get("cost_price"),
                        "quantity": h.get("quantity"),
                    }
            except Exception as e:  # noqa: BLE001
                log.warning("持仓读取失败: %s", e)

        if self.watch_mode in ("watchlist", "both", "pool"):
            raw = self.watchlist_cfg
            if self.watch_mode == "pool":
                pools = self.cfg.get("stock_pools") or {}
                raw = [c for lst in pools.values() for c in (lst or [])]
            for entry in raw:
                if isinstance(entry, dict):
                    code = str(entry.get("code") or "").strip()
                    name, market = entry.get("name") or code, entry.get("market")
                else:
                    code, name, market = str(entry).strip(), str(entry), None
                if not code:
                    continue
                items.setdefault(code, {
                    "code": code, "name": name,
                    "market": market or _guess_market(code),
                    "cost_price": None, "quantity": None,
                })
        return list(items.values())

    # ------------------------------------------------------------ 慢轨喂价位
    def plan_levels(self) -> dict[str, dict]:
        """从**当日**持仓量化缓存里取出关键位，生成 ``price_cross`` 用的价位表。

        慢轨每个交易日算一次持仓量化（含止损参考、卖出区间、目标价）并落盘，快轨直接读，
        不自己算——K 线类计算留在慢轨，快轨必须保持毫秒级。

        只认当天缓存（``cached_items`` 会校验日期）：昨日价位对今天的盯盘是噪音甚至误导。

        映射口径：
        * ``below`` ← ``stop_loss``（跌破止损参考）
        * ``above`` ← ``zone_lo`` 卖出区间下沿，缺失时退到 ``target_1`` 第一目标价
        """
        if not self.levels_from_cache:
            return {}
        from .holdings_quant import cached_items

        today = self._now_fn().strftime("%Y-%m-%d")
        out: dict[str, dict] = {}
        for market in ("CN", "HK", "US"):
            try:
                items = cached_items(market, today)
            except Exception as e:  # noqa: BLE001
                log.debug("读取[%s]持仓量化缓存失败: %s", market, e)
                continue
            for it in items or []:
                code = str(it.get("code") or "").strip()
                if not code:
                    continue
                lv: dict = {}
                stop = it.get("stop_loss")
                if stop:
                    lv["below"] = float(stop)
                    lv["below_label"] = "止损位"
                above = it.get("zone_lo") or it.get("target_1")
                if above:
                    lv["above"] = float(above)
                    lv["above_label"] = (
                        "卖出区间下沿" if it.get("zone_lo") else "第一目标价"
                    )
                if lv:
                    out[code] = lv
        if out:
            log.debug("慢轨喂入关键位 %d 只", len(out))
        return out

    # ------------------------------------------------------------ 对外摘要
    def describe(self) -> str:
        """一行配置摘要，给配置页 / CLI 展示用。

        调用方（看板页面）不要去猜属性名 —— 之前就是页面写了
        ``idle_interval_sec``（那是配置键）而 watcher 上的属性叫 ``idle_interval``，
        直接 AttributeError。想加展示项就在这里加。
        """
        return (
            f"盯盘清单 {len(self.watchlist())} 只 · 盘中每 {self.interval} 秒一轮 · "
            f"空闲每 {self.idle_interval} 秒 · 冷却 {self.cooldown / 60:.0f} 分钟"
        )

    # ------------------------------------------------------------ 运行状态
    def status(self) -> dict:
        """快轨现在是否在跑、跑到哪了 —— 对外稳定契约。

        调用方不要自己去读 ``realtime_state.json``（结构会变）、也不要猜引擎属性名，
        想加展示项就在这里加。``alive`` 的判定：心跳年龄 ≤ max(空闲间隔×3, 120) 秒。
        """
        now = self._now_fn()
        st = self._load_state(now.strftime("%Y-%m-%d"))
        hb = st.get("heartbeat")
        age: Optional[float] = None
        if hb:
            try:
                tz = now.tzinfo
                last = datetime.strptime(str(hb), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                age = max(0.0, (now - last).total_seconds())
            except (TypeError, ValueError):
                age = None
        tolerance = float(max(int(self.idle_interval) * 3, 120))
        open_markets = [m for m in ("CN", "HK", "US") if in_session(m, now, self.us_winter)]
        try:
            watch_count = len(self.watchlist())
        except Exception as e:  # noqa: BLE001
            log.debug("读取盯盘清单失败（状态照常展示）: %s", e)
            watch_count = 0
        return {
            "enabled": bool(self.enabled),
            "dry_run": bool(self.dry_run),
            "alive": age is not None and age <= tolerance,
            "heartbeat": hb,
            "heartbeat_age_sec": age,
            "rounds": int(st.get("rounds") or 0),
            "started_at": st.get("started_at"),
            "session_open": bool(st.get("session_open")),
            "in_session": bool(open_markets),
            "open_markets": open_markets,
            "watch_count": watch_count,
            "watch_mode": self.watch_mode,
            "interval": self.interval,
            "idle_interval": self.idle_interval,
            "cooldown_min": self.cooldown / 60,
            "last_targets": int(st.get("last_targets") or 0),
            "last_quotes": int(st.get("last_quotes") or 0),
            "last_alerts": int(st.get("last_alerts") or 0),
            "last_push_at": st.get("last_push_at"),
            "last_push_n": int(st.get("last_push_n") or 0),
            "stale_after_sec": tolerance,
            "state_path": str(self._state_path()),
            "config_path": self.config_path,
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def status_line(self) -> str:
        """一行中文状态，看板/CLI 直接显示（比 status() 更适合给人看）。"""
        s = self.status()
        if not s["enabled"]:
            return "快轨未启用 —— notify.yaml → realtime.enabled 为 false"
        if not s["heartbeat"]:
            return (
                "快轨从未跑过 —— 没有状态文件心跳。"
                f"（应位于 {s['state_path']}；桌面端由 GP助手 主程序拉起快轨，"
                "或单独跑实时盯盘命令）"
            )
        age = float(s["heartbeat_age_sec"] or 0)
        ago = f"{age:.0f} 秒前" if age < 90 else f"{age / 60:.1f} 分钟前"
        state = "运行中" if s["alive"] else "已停摆"
        market = ("开市中 " + "/".join(s["open_markets"])) if s["in_session"] else "当前休市"
        push = f"最近推送 {s['last_push_at']}" if s["last_push_at"] else "今日尚未推送"
        return (
            f"{state} · 最近一轮 {s['heartbeat']}（{ago}）· 今日第 {s['rounds']} 轮 · "
            f"清单 {s['watch_count']} 只 · {market} · {push}"
        )

    # ------------------------------------------------------------ 状态
    def _state_path(self) -> Path:
        base = os.environ.get("QTS_DATA_DIR") or str(Path(self.config_path).parent)
        return Path(base) / "realtime_state.json"

    # 状态文件的空白骨架。心跳字段别擅自删：看板「运行状态」与 CLI --status 都读它。
    _STATE_BLANK: dict[str, Any] = {
        "prev_price": {},
        "peak_price": {},
        "cooldown": {},
        "history": {},
        "started_at": None,     # 本次进程首轮时间
        "heartbeat": None,      # 最近一轮时间（北京时间）
        "rounds": 0,            # 当日累计轮数
        "session_open": False,  # 最近一轮是否有开市市场
        "last_targets": 0,      # 最近一轮盯盘清单只数（开市部分）
        "last_quotes": 0,       # 最近一轮成功取到价的行数
        "last_alerts": 0,       # 最近一轮规则命中条数（去重前）
        "last_push_at": None,   # 最近一次真正推送出去的时间
        "last_push_n": 0,       # 最近一次推送条数
    }

    def _blank_state(self, day: str) -> dict:
        return {"day": day, **deepcopy(self._STATE_BLANK)}

    def _load_state(self, day: str) -> dict:
        st = self._blank_state(day)
        p = self._state_path()
        try:
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if raw.get("day") == day:  # 跨交易日则整体重置（峰值/前值/冷却都过期）
                    # 用 is not None 而不是 or：rounds=0 / session_open=False 这类假值
                    # 必须原样保留，否则每读一次盘就把计数清掉
                    st.update({k: raw.get(k) if raw.get(k) is not None else st[k] for k in st})
                    return st
        except Exception as e:  # noqa: BLE001
            log.debug("盯盘状态读取失败（按空状态继续）: %s", e)
        return st

    def _save_state(self, st: dict) -> None:
        try:
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("盯盘状态保存失败: %s", e)

    # ------------------------------------------------------------ 心跳
    def _note_round(
        self, st: dict, now: datetime, *, session_open: bool,
        targets: int = 0, quotes: int = 0, alerts: int = 0, pushed: int = 0,
    ) -> None:
        """记一轮心跳。**每一轮都要调**，包括休市/清单为空/行情失败这些早退分支 ——
        否则"状态文件停更"和"进程已死"在外部看起来一模一样。"""
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        if not st.get("started_at"):
            st["started_at"] = stamp
        st["heartbeat"] = stamp
        st["rounds"] = int(st.get("rounds") or 0) + 1
        st["session_open"] = bool(session_open)
        st["last_targets"] = int(targets)
        st["last_quotes"] = int(quotes)
        st["last_alerts"] = int(alerts)
        if pushed:
            st["last_push_at"] = stamp
            st["last_push_n"] = int(pushed)
        self.last_stats = {
            "rounds": st["rounds"],
            "session_open": st["session_open"],
            "targets": int(targets),
            "quotes": int(quotes),
            "alerts": int(alerts),
            "pushed": int(pushed),
            "heartbeat": stamp,
        }

    def _beat_idle(
        self, now: datetime, *, session_open: bool,
        targets: int = 0, quotes: int = 0, alerts: int = 0,
    ) -> None:
        """早退分支的心跳：读盘 → 记一轮 → 落盘（dry-run 不落盘）。"""
        st = self._load_state(now.strftime("%Y-%m-%d"))
        self._note_round(st, now, session_open=session_open,
                         targets=targets, quotes=quotes, alerts=alerts)
        if not self.dry_run:
            self._save_state(st)

    # ------------------------------------------------------------ 一轮
    def tick(self, *, force: bool = False) -> list[Alert]:
        """跑一轮盯盘；返回本轮实际推送的告警（未推送的返回空）。"""
        if not self.enabled and not force:
            return []
        now = self._now_fn()
        now_ts = now.timestamp()
        day = now.strftime("%Y-%m-%d")

        targets = self.watchlist()
        if not targets:
            log.debug("盯盘清单为空，跳过本轮")
            self._beat_idle(now, session_open=False)
            return []

        open_markets = {
            str(t.get("market") or "CN")
            for t in targets
            if in_session(str(t.get("market") or "CN"), now, self.us_winter)
        }
        active = [t for t in targets if str(t.get("market") or "CN") in open_markets]
        if not active and not force:
            log.debug("无开市市场，跳过本轮（%s）", day)
            self._beat_idle(now, session_open=False, targets=len(targets))
            return []
        if force:
            active = targets

        codes = [t["code"] for t in active]
        try:
            df = self.quote_fetcher(codes)
        except Exception as e:  # noqa: BLE001
            log.warning("行情获取异常，本轮跳过: %s", e)
            self._beat_idle(now, session_open=bool(open_markets), targets=len(active))
            return []
        if df is None or getattr(df, "empty", True):
            log.warning("行情获取为空，本轮跳过（%d 只）", len(codes))
            self._beat_idle(now, session_open=bool(open_markets), targets=len(active))
            return []

        quotes = {str(r["code"]): r for r in df.to_dict("records")}
        st = self._load_state(day)
        auto_levels = self.plan_levels()
        alerts: list[Alert] = []
        for t in active:
            row = quotes.get(t["code"])
            if row is None or row.get("close") is None:
                continue
            ctx = {
                "market": t.get("market") or "CN",
                "cost_price": t.get("cost_price"),
                "prev_price": st["prev_price"].get(t["code"]),
                "peak_price": st["peak_price"].get(t["code"]),
                "history": st["history"].get(t["code"]) or [],
                "auto_levels": auto_levels,
                "now_ts": now_ts,
            }
            alerts.extend(self.engine.evaluate(row, ctx))

            # 更新状态：前值 / 日内峰值 / 价格序列
            px = float(row["close"])
            st["prev_price"][t["code"]] = px
            st["peak_price"][t["code"]] = max(px, float(st["peak_price"].get(t["code"]) or px))
            hist = st["history"].setdefault(t["code"], [])
            hist.append([now_ts, px])
            if len(hist) > _HISTORY_CAP:
                del hist[: len(hist) - _HISTORY_CAP]

        # 注意：_dispatch 必须复用本轮已加载的 st（它要往 st["cooldown"] 里写），
        # 否则先写盘再被 _save_state(st) 用旧冷却覆盖，冷却去重会失效。
        pushed = self._dispatch(alerts, now_ts, st)
        self._note_round(st, now, session_open=bool(open_markets),
                         targets=len(active), quotes=len(quotes),
                         alerts=len(alerts), pushed=len(pushed))
        if not self.dry_run:
            # dry-run 不落盘：探测一轮不该留下冷却/前值去污染真实盯盘
            self._save_state(st)
        return pushed

    # ------------------------------------------------------------ 冷却 + 推送
    def _dispatch(self, alerts: list[Alert], now_ts: float, st: dict) -> list[Alert]:
        fresh: list[Alert] = []
        for a in alerts:
            key = f"{a.code}|{a.rule}"
            last = float(st["cooldown"].get(key) or 0)
            if now_ts - last < self.cooldown:
                continue   # 冷却窗口内：同一只票同一条规则不重复推
            fresh.append(a)
        for a in fresh:
            st["cooldown"][f"{a.code}|{a.rule}"] = now_ts

        immediate = [a for a in fresh if a.severity <= SEV_ACTION]
        buffered = [a for a in fresh if a.severity > SEV_ACTION]
        if self.digest <= 0:  # 未开摘要：非致命告警也立即推
            immediate, buffered = fresh, []

        # 全局限流：距上次推送太近时，只有"需立即处理"能穿透，其余转缓冲（不丢）
        if immediate and now_ts - self._last_push < self.min_push_gap:
            buffered.extend(a for a in immediate if a.severity > SEV_ACTION)
            immediate = [a for a in immediate if a.severity <= SEV_ACTION]

        sent: list[Alert] = []
        if immediate:
            if self._send(immediate, digest=False):
                self._last_push = now_ts
                sent.extend(immediate)

        self._buffer.extend(buffered)
        if self.digest > 0:
            if self._last_digest <= 0:  # 首次：只记录起点，避免启动瞬间就吐一条空摘要
                self._last_digest = now_ts
            elif self._buffer and now_ts - self._last_digest >= self.digest:
                if self._send(self._buffer, digest=True):
                    self._last_digest = now_ts
                    sent.extend(self._buffer)
                    self._buffer = []
        elif self._buffer and now_ts - self._last_push >= self.min_push_gap:
            # 未开摘要：被限流转存的告警在限流解除后立即补推
            if self._send(self._buffer, digest=False):
                self._last_push = now_ts
                sent.extend(self._buffer)
                self._buffer = []
        return sent

    def _send(self, alerts: list[Alert], *, digest: bool) -> bool:
        stamp = self._now_fn().strftime("%Y-%m-%d %H:%M:%S")
        if digest:
            title = f"盯盘摘要 · {len(alerts)} 条"
        elif len(alerts) == 1:
            a = alerts[0]
            title = f"{_SEV_TAG[a.severity]} {a.title} · {a.name}({a.code})"
        else:
            worst = min(a.severity for a in alerts)
            title = f"{_SEV_TAG[worst]} 盯盘预警 · {len(alerts)} 条"

        text = "\n".join(
            f"[{i}] {a.line()}" for i, a in enumerate(alerts, 1)
        ) + f"\n\n时间：{stamp}（北京时间）"
        html = _build_html(alerts, stamp)

        if self.dry_run:
            log.info("[dry-run] 命中 %d 条，不推送：\n%s", len(alerts), text)
            return True
        if self.notifier is None:
            log.info("无可用通知渠道，仅打印：\n%s\n%s", title, text)
            return True
        res = self.notifier.send(title, text, html)
        failed = [k for k, v in (res or {}).items() if v != "ok"]
        if failed and len(failed) == len(res or {}):
            log.error("盯盘推送全部失败: %s", failed)
            return False
        return True

    # ------------------------------------------------------------ 循环
    def _sleep_secs(self) -> int:
        """分时段轮询：开市中用 interval，否则用 idle_interval（午休/非交易时段降频）。"""
        if not self.enabled and not self.dry_run:
            # 待命状态：即使有市场开着也别用 5 秒间隔空转，只是白读配置
            return self.idle_interval
        now = self._now_fn()
        for m in ("CN", "HK", "US"):
            if in_session(m, now, self.us_winter):
                return self.interval
        return self.idle_interval

    def run_forever(self, *, reload_every: int = 60, heartbeat_log_sec: int = 300) -> None:
        """常驻循环。

        ``realtime.enabled=false`` 时**不退场**，而是常驻待命：不调 ``tick()``
        （待命期间零行情请求），但每轮都重读配置 —— 这样在配置页打开开关保存后，
        最迟一个空闲间隔就自动开始盯，不必重启进程。进程本身因此永远是"活着"的，
        不会和"已经挂了"混淆；反过来，有进程但没心跳才是真出事了。
        """
        if not self.enabled and not self.dry_run:
            log.info(
                "实时盯盘未启用（notify.yaml → realtime.enabled=false）—— 常驻待命，"
                "开关打开后自动生效；待命期间不产生任何行情请求"
            )
        else:
            log.info(
                "实时盯盘启动 | 盘中每 %ds / 空闲每 %ds | 冷却 %.0f 分钟 | 盯 %d 只 | 模式 %s | 心跳 %s",
                self.interval, self.idle_interval, self.cooldown / 60,
                len(self.watchlist()), self.watch_mode, self._state_path(),
            )
        n = 0
        beat_at = 0.0
        try:
            while not self._stop.is_set():
                # 未启用时每轮都重载：否则要等满 reload_every 轮才发现开关被打开
                if n % max(1, reload_every) == 0 or not self.enabled:
                    try:
                        self.reload()
                    except Exception as e:  # noqa: BLE001
                        log.warning("配置重载失败（沿用旧配置）: %s", e)
                if self.enabled or self.dry_run:
                    try:
                        self.tick()
                    except Exception as e:  # noqa: BLE001
                        log.error("盯盘轮次执行失败: %s", e)
                n += 1
                # 定期心跳日志：无事发生时 tick 是静默的，没这条就无法区分"在盯"
                # 和"已经死了"——只能干等邮件，那是不可接受的观测性
                mono = time.monotonic()
                if mono - beat_at >= max(0, heartbeat_log_sec) and self.last_stats:
                    beat_at = mono
                    s = self.last_stats
                    log.info(
                        "盯盘心跳 | 第 %s 轮 | %s | 清单 %s 只 · 取价 %s · 命中 %s · 推送 %s | %s",
                        s.get("rounds", 0),
                        "开市" if s.get("session_open") else "休市",
                        s.get("targets", 0), s.get("quotes", 0),
                        s.get("alerts", 0), s.get("pushed", 0),
                        s.get("heartbeat") or "—",
                    )
                self._stop.wait(self._sleep_secs())
        except KeyboardInterrupt:
            pass
        log.info("实时盯盘已退出 | 本次共 %d 轮 | 状态文件 %s", n, self._state_path())

    def stop(self) -> None:
        """供后台线程调用：结束循环。"""
        self._stop.set()


def install_sigterm_stop(watcher: RealtimeWatcher) -> bool:
    """让 ``kill <pid>``（SIGTERM，ctl.py 停服务用的就是这个）走优雅退出。

    置停止位 → 循环退出 → 打一行"已退出"再收尾，而不是被 SIGKILL 硬砍。
    只能在主线程安装（signal 的硬限制），非主线程静默跳过并返回 False ——
    桌面端的 ``RealtimeThread`` 正属此类，它用自己的 stop_event。
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        signal.signal(signal.SIGTERM, lambda *_: watcher.stop())
    except (ValueError, OSError):
        return False
    return True


def _guess_market(code: str) -> str:
    """粗判市场：5 位数字=港股，纯字母=美股，其余=A 股。"""
    s = str(code).strip()
    if s.isalpha():
        return "US"
    digits = "".join(ch for ch in s if ch.isdigit())
    return "HK" if len(digits) == 5 else "CN"


def _build_html(alerts: list[Alert], stamp: str) -> str:
    """邮件用 HTML：A 股口径 —— 涨红跌绿。"""
    rows = []
    for a in alerts:
        pct = a.pct_chg
        color = "#d9363e" if (pct or 0) > 0 else ("#0a8f4d" if (pct or 0) < 0 else "#666")
        pct_txt = f"{pct:+.2f}%" if pct is not None else "—"
        rows.append(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eee">'
            f'{_SEV_TAG[a.severity]} {a.name}<br><span style="color:#999;font-size:12px">{a.code}</span></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right">'
            f'{a.price if a.price is not None else "—"}<br>'
            f'<span style="color:{color};font-size:12px">{pct_txt}</span></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee">'
            f'<b>{a.title}</b><br><span style="font-size:12px">{a.detail}</span></td></tr>'
        )
    return (
        '<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:720px">'
        f'<h3 style="margin:0 0 8px">实时盯盘 · {len(alerts)} 条</h3>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px">'
        '<tr style="background:#fafafa"><th align="left">标的</th>'
        '<th align="right">现价</th><th align="left">信号</th></tr>'
        + "".join(rows) +
        f'</table><p style="color:#999;font-size:12px">时间：{stamp}（北京时间）</p></div>'
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="实时盯盘引擎（快轨）")
    ap.add_argument("--config", default="config/notify.yaml", help="notify.yaml 路径")
    ap.add_argument("--once", action="store_true", help="只跑一轮（含推送）后退出")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送，忽略 enabled 开关")
    ap.add_argument("--interval", type=int, default=0, help="覆盖盘中轮询间隔（秒）")
    ap.add_argument("--status", action="store_true", help="只看状态：在不在跑、跑到哪了")
    args = ap.parse_args(argv)

    w = RealtimeWatcher(args.config, dry_run=args.dry_run)
    if args.status:
        s = w.status()
        print(w.status_line())
        for k, v in s.items():
            print(f"  {k:<20} {v}")
        return 0
    if args.interval:
        w.interval = max(1, args.interval)
    if args.once or args.dry_run:
        hits = w.tick(force=True)
        print(f"本轮命中并推送 {len(hits)} 条")
        for a in hits:
            print(f"  [{_SEV_LABEL[a.severity]}] {a.name}({a.code}) {a.title} — {a.detail}")
        return 0
    install_sigterm_stop(w)          # ctl.py stop 走 SIGTERM，这里让它优雅退出
    w.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
