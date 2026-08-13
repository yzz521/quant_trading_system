"""Trade monitor — watch macOS notification centre for broker trade alerts.

The scheduler already knows what we *think* we hold (config/holdings.db).
This module keeps that database in sync with *actual* trades by watching the
macOS notification centre:

* 同花顺 / 平安证券 push "成交回报" messages to their WeChat 服务号;
* those messages arrive in WeChat desktop and surface as macOS notifications;
* we poll the notification-centre database incrementally, parse buy/sell
  texts and apply them to :class:`Holdings` automatically.

Prerequisites
-------------
* macOS Big Sur+ — the notification DB lives at
  ``~/Library/Group Containers/group.com.apple.usernoted/db2/db``.
* The shell/terminal running this script must have **Full Disk Access**
  (System Settings → Privacy & Security → Full Disk Access). Without it the
  DB can't be opened even though the file looks readable.
* WeChat desktop must stay logged in so 服务号 messages keep arriving.
* The broker's WeChat 服务号 must have 交易提醒/成交回报 enabled.

Safety
------
* Only high-confidence parses (side **and** code both found) are applied.
* Every notification examined is recorded in a ``trade_log`` table inside
  ``holdings.db`` — nothing is double-counted and skipped messages stay
  auditable. The ``notify_rowid`` unique index gives idempotency.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..utils import get_logger, load_yaml
from .holdings import Holdings

log = get_logger("TradeMonitor")

BEIJING = timezone(timedelta(hours=8))
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

DEFAULT_NOTIFY_DB = str(
    Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"
)

# 默认只处理微信的通知；实际 bundle id 可能带扩展后缀，按前缀匹配
DEFAULT_WHITELIST_APPS = ["com.tencent.xinWeChat", "com.tencent.xinwechat"]

_TRADE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    notify_rowid  INTEGER UNIQUE,          -- 通知中心 db 的 rowid（幂等去重）
    app_id        TEXT DEFAULT '',
    ts            TEXT DEFAULT '',          -- 通知时间（北京时间）
    raw_text      TEXT DEFAULT '',          -- 原始通知全文
    side          TEXT DEFAULT '',          -- BUY / SELL / ''
    code          TEXT DEFAULT '',
    name          TEXT DEFAULT '',
    quantity      REAL DEFAULT 0,
    price         REAL DEFAULT 0,
    action        TEXT DEFAULT '',          -- APPLIED / SKIPPED_NOT_TRADE / SKIPPED_NO_HOLDING / APPLIED_DELETE / ERROR
    message       TEXT DEFAULT ''
)
"""


# ---------------------------------------------------------------------- #
# 交易动作
# ---------------------------------------------------------------------- #
@dataclass
class TradeAction:
    """A single parsed buy/sell event."""
    side: str                    # BUY / SELL
    code: str
    name: str = ""
    quantity: float = 0.0
    price: float = 0.0
    ts: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        self.side = self.side.upper()


# ---------------------------------------------------------------------- #
# 解析器
# ---------------------------------------------------------------------- #
class TradeParser:
    """Parse a broker "成交回报" message into a TradeAction.

    Works on the noisy, format-variant texts that 同花顺/平安证券 服务号 push
    through WeChat. Matching is deliberately tolerant — as long as a buy/sell
    keyword **and** a security code are both found the text is considered a
    trade; quantity/price/name are best-effort.
    """

    # 常见消息形态（用于注释，不用于匹配）：
    #   "您的委托已成交：买入 600519 贵州茅台 100股 成交价1500.00元"
    #   "证券名称：工商银行 证券代码：601398 买卖标志：买入 成交数量：500 成交价格：5.10"
    #   "【同花顺】您的买入委托已成交：贵州茅台(600519) 100股 1500.00"
    #   "平安证券：您的卖出委托已成交 平安银行 200股 12.50元"

    _CN_CODE = r"(?:6\d{5}|0\d{5}|3\d{5}|688\d{3}|8\d{5}|4\d{5}|1\d{5}|5\d{5})"  # 沪深京 A 股 + 场内基金/ETF
    _HK_CODE = r"(?:00\d{3}|01\d{3}|02\d{3}|03\d{3}|09\d{3}|0\d{4})"
    _US_CODE = r"[A-Z]{1,5}"

    def __init__(self, cfg: Optional[dict] = None) -> None:
        cfg = cfg or {}
        kws = cfg.get("keywords", {}) or {}
        self.buy_kws = kws.get("buy") or ["买入", "买进", "申购", "增持", "建仓"]
        self.sell_kws = kws.get("sell") or ["卖出", "卖出了", "赎回", "减持", "清仓"]
        # 通知正文里出现这些名字才解析（券商服务号名），空 = 不限制
        hints = cfg.get("app_name_hint") or []
        self.app_hints = hints if isinstance(hints, list) else [hints]

        # 方向：先匹配多字关键词，再匹配单字 买/卖
        buy_alt = "|".join(re.escape(k) for k in sorted(self.buy_kws, key=len, reverse=True))
        sell_alt = "|".join(re.escape(k) for k in sorted(self.sell_kws, key=len, reverse=True))
        self._re_side = re.compile(
            rf"(?P<buy>{buy_alt})|(?P<sell>{sell_alt})|(?P<buy1>买入?|建仓)|(?P<sell1>卖出?|清仓)"
        )
        # 代码：优先"证券代码："形式，其次括号，其次裸 6 位/港股/美股
        self._re_code = re.compile(
            rf"证券代码[:：]?\s*(?P<c1>{self._CN_CODE}|{self._HK_CODE}|{self._US_CODE})\b"
            rf"|[（(](?P<c2>{self._CN_CODE}|{self._HK_CODE})[)）]"
            rf"|\b(?P<c3>{self._CN_CODE})\b"
            rf"|\b(?P<c4>{self._HK_CODE})\b"
            rf"|\b(?P<c5>{self._US_CODE})\b"
        )
        # 数量："100股 / 1手 / 成交数量：500 / 数量500"
        self._re_qty = re.compile(
            r"成交数量[:：]?\s*(?P<q1>\d+(?:\.\d+)?)"
            r"|数量[:：]?\s*(?P<q2>\d+(?:\.\d+)?)"
            r"|(?P<q3>\d+(?:\.\d+)?)\s*股"
            r"|(?P<q4>\d+(?:\.\d+)?)\s*手"
            r"|(?P<q5>\d+(?:\.\d+)?)\s*份"
        )
        # 价格："成交价1500.00 / 成交价格：5.10 / 价格 12.50元 / 净值 3.90"
        self._re_price = re.compile(
            r"成交(?:价格|价)[:：]?\s*(?P<p1>\d+(?:\.\d+)?)"
            r"|价格[:：]?\s*(?P<p2>\d+(?:\.\d+)?)"
            r"|净值[:：]?\s*(?P<p4>\d+(?:\.\d+)?)"
            r"|(?P<p3>\d+\.\d{2,4})\s*元"
        )
        # 名称：仅"证券名称：/股票："带标签形式；其余靠代码邻域提取
        self._re_name = re.compile(
            r"证券名称[:：]?\s*(?P<n1>[\u4e00-\u9fa5A-Za-z0-9]{2,10})"
            r"|股票(?:名称)?[:：]?\s*(?P<n2>[\u4e00-\u9fa5]{2,10})"
        )

    # ------------------------------------------------------------------ #
    # 常见的非股票名词语（代码邻域提取时排除）
    _STOP_NAMES = {"成交", "委托", "买入", "卖出", "申购", "赎回", "数量",
                   "价格", "股票", "证券", "您的", "确认", "已成交",
                   "净值", "成交价", "成交价格", "委托价", "证券代码"}

    def _find_side(self, text: str) -> Optional[str]:
        m = self._re_side.search(text)
        if not m:
            return None
        if m.group("buy") or m.group("buy1"):
            return "BUY"
        return "SELL"

    def _find_code(self, text: str) -> Optional[str]:
        m = self._re_code.search(text)
        if not m:
            return None
        for g in ("c1", "c2", "c3", "c4", "c5"):
            if m.group(g):
                return m.group(g)
        return None

    def _find_qty(self, text: str) -> float:
        m = self._re_qty.search(text)
        if not m:
            return 0.0
        for g in ("q1", "q2", "q3", "q4", "q5"):
            if m.group(g):
                v = float(m.group(g))
                return v * 100 if g == "q4" else v   # 手 → ×100
        return 0.0

    def _find_price(self, text: str) -> float:
        m = self._re_price.search(text)
        if not m:
            return 0.0
        for g in ("p1", "p2", "p4", "p3"):
            if m.group(g):
                return float(m.group(g))
        return 0.0

    def _find_name(self, text: str, code: str = "") -> str:
        # 1) 带标签形式：证券名称：/股票：
        m = self._re_name.search(text)
        if m:
            for g in ("n1", "n2"):
                if m.group(g):
                    return m.group(g).strip()
        # 2) 代码邻域：先看代码后方（名称通常在代码后），再看前方
        if code:
            i = text.find(code)
            if i >= 0:
                seg_after = text[i + len(code): i + len(code) + 20]
                seg_before = text[max(0, i - 20): i]
                for chunk in re.findall(r"[\u4e00-\u9fa5]{2,8}", seg_after):
                    if not any(s in chunk for s in self._STOP_NAMES):
                        return chunk
                for chunk in reversed(re.findall(r"[\u4e00-\u9fa5]{2,8}", seg_before)):
                    if not any(s in chunk for s in self._STOP_NAMES):
                        return chunk
        return ""

    # ------------------------------------------------------------------ #
    def parse_structured(self, text: str) -> Optional[TradeAction]:
        """Parse the key:value format used by 同花顺『成交提醒』等推送.

        Example input::

            成交提醒
            股票代码：    513310
            股票名称：    中韩半导体ETF华泰柏瑞
            交易方向：    买入，委托数量200股
            成交量：    已成交200股，已全部成交
            成交金额：    937.40元（成交价格：4.687元）
            交易时间：    2026-07-31 13:51:35
        """
        # 逐行提取 键：值
        info: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            for sep in ("：", ":"):
                if sep in line:
                    k, v = line.split(sep, 1)
                    info[k.strip()] = v.strip()
                    break

        code = info.get("股票代码") or info.get("证券代码") or ""
        side_raw = info.get("交易方向") or info.get("买卖标志") or ""
        if not code or not side_raw:
            return None
        # 方向判断（行内可能混入其它字段，如“买入，委托数量200股”）
        if any(k in side_raw for k in self.buy_kws):
            side = "BUY"
        elif any(k in side_raw for k in self.sell_kws):
            side = "SELL"
        else:
            return None

        name = (info.get("股票名称") or info.get("证券名称") or "").strip()
        qty_text = info.get("成交量") or info.get("成交数量") \
            or info.get("委托数量") or text
        qty = self._qty_from(qty_text)
        price = self._price_from(text)
        return TradeAction(side=side, code=code, name=name, quantity=qty,
                           price=price, raw=text)

    @staticmethod
    def _qty_from(text: str) -> float:
        """Extract a share count from a 成交量/数量 line."""
        # 已成交200股 / 200股 / 成交数量：500 / 2手
        for pat in (r"已成交\s*(\d+(?:\.\d+)?)\s*股",
                    r"(\d+(?:\.\d+)?)\s*手",
                    r"(\d+(?:\.\d+)?)\s*股",
                    r"(\d+(?:\.\d+)?)\s*份",
                    r"(\d+(?:\.\d+)?)"):
            m = re.search(pat, text)
            if m:
                v = float(m.group(1))
                return v * 100 if "手" in pat else v
        return 0.0

    @staticmethod
    def _price_from(text: str) -> float:
        """Extract the fill price from a 成交金额/成交价格 line."""
        for pat in (r"成交价格[：:]\s*(\d+(?:\.\d+)?)",
                    r"成交价[：:]\s*(\d+(?:\.\d+)?)",
                    r"价格[：:]\s*(\d+(?:\.\d+)?)",
                    r"(\d+(?:\.\d+)?)\s*元"):
            m = re.search(pat, text)
            if m:
                return float(m.group(1))
        return 0.0

    # ------------------------------------------------------------------ #
    def parse(self, text: str, ts: str = "") -> Optional[TradeAction]:
        """Parse a raw notification body → TradeAction, or None if it does
        not look like a trade message."""
        if not text:
            return None
        # 券商名提示过滤（可选）：正文不包含任何一个提示词则忽略
        if self.app_hints and not any(h and h in text for h in self.app_hints):
            return None
        # 1) 结构化键值对格式（成交提醒）优先
        st = self.parse_structured(text)
        if st is not None:
            return st
        # 2) 自由文本格式（买入 600519 贵州茅台 100股 成交价1500.00）
        side = self._find_side(text)
        code = self._find_code(text)
        if not side or not code:
            return None
        return TradeAction(
            side=side,
            code=code,
            name=self._find_name(text, code),
            quantity=self._find_qty(text),
            price=self._find_price(text),
            ts=ts,
            raw=text,
        )


# ---------------------------------------------------------------------- #
# 通知中心读取器
# ---------------------------------------------------------------------- #
# ---------------------------------------------------------------------- #
# 监听器：读取 → 解析 → 同步持仓
# ---------------------------------------------------------------------- #
class TradeMonitor:
    """解析成交文本并同步持仓（main-v3 精简版：仅 parser + apply_trade）。

    Usage::

        mon = TradeMonitor()
        t = mon.parser.parse("...成交文本...")   # → TradeAction
        mon.apply_trade(t)                       # 写入持仓
    """

    def __init__(self, config_path: str = "config/notify.yaml",
                 notify_db: Optional[str] = None) -> None:
        cfg = load_yaml(config_path) or {}
        tcfg = cfg.get("trade_monitor", {}) or {}

        self.parser = TradeParser(tcfg)
        self.app_filter = tcfg.get("whitelist_apps") or DEFAULT_WHITELIST_APPS
        self.auto_sync = bool(tcfg.get("auto_sync", True))
        self.lookback_min = int(tcfg.get("lookback_min", 2880))
        self.poll_interval = int(tcfg.get("poll_interval_sec", 30))

        holdings_path = str(Path(config_path).parent / "holdings.yaml")
        self.holdings = Holdings(holdings_path)
        self._ensure_trade_log()
        self._preview_warned = False

    # 微信预览关闭时通知中心只存这些占位文案，看不到真实内容
    _HIDDEN_BODY_MARKERS = ("你收到了一条消息", "你收到一条新消息", "收到一条新消息")

    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.holdings.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_trade_log(self) -> None:
        with self._conn() as conn:
            conn.execute(_TRADE_LOG_SCHEMA)

    def _last_rowid(self) -> int:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT COALESCE(MAX(notify_rowid),0) FROM trade_log"
            ).fetchone()
            return int(r[0] or 0)

    def _record(self, nrow: int, app: str, ts: str, raw: str,
                side: str, code: str, name: str, qty: float, price: float,
                action: str, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO trade_log"
                "(notify_rowid,app_id,ts,raw_text,side,code,name,quantity,price,action,message)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (nrow, app, ts, raw[:2000], side, code, name, qty, price,
                 action, message[:500]),
            )

    # ------------------------------------------------------------------ #
    def apply_trade(self, trade: TradeAction) -> tuple[str, str]:
        """Apply a parsed trade to Holdings. Returns (action, message)."""
        code = trade.code
        existing = next((p for p in self.holdings.all() if p["code"] == code), None)

        if trade.side == "BUY":
            qty = trade.quantity
            price = trade.price
            if existing:
                old_qty = float(existing["quantity"])
                old_cost = float(existing["cost_price"])
                new_qty = old_qty + qty
                # 加权平均成本
                new_cost = (old_cost * old_qty + price * qty) / new_qty if qty > 0 else old_cost
                self.holdings.update(code, quantity=int(new_qty),
                                     cost_price=round(new_cost, 4),
                                     name=trade.name or existing.get("name", ""))
                msg = (f"加仓 {code} {trade.name} {int(qty)}股 @{price}，"
                       f"现 {int(new_qty)}股，均价 {new_cost:.4f}")
            else:
                self.holdings.add(code, name=trade.name, market=self._guess_market(code),
                                  cost_price=price, quantity=int(qty),
                                  buy_date=datetime.now(BEIJING).strftime("%Y-%m-%d"))
                msg = f"新建仓 {code} {trade.name} {int(qty)}股 @{price}"
            return "APPLIED", msg

        # ---- SELL ----
        if not existing:
            return ("SKIPPED_NO_HOLDING",
                    f"收到卖出 {code} {trade.name} 但本地无该持仓，跳过")
        old_qty = float(existing["quantity"])
        qty = trade.quantity or old_qty  # 未带数量按清仓处理
        if qty >= old_qty:
            self.holdings.delete([code])
            return ("APPLIED_DELETE",
                    f"清仓 {code} {trade.name}（{int(old_qty)}股全部卖出 @{trade.price}）")
        new_qty = int(old_qty - qty)
        self.holdings.update(code, quantity=new_qty)
        return ("APPLIED",
                f"减仓 {code} {trade.name} {int(qty)}股 @{trade.price}，剩 {new_qty}股")

    @staticmethod
    def _guess_market(code: str) -> str:
        if code.isdigit():
            return "HK" if len(code) == 5 else "CN"
        return "US"

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
# ---------------------------------------------------------------------- #
# 自测：解析器在各种消息形态下的表现
# ---------------------------------------------------------------------- #
_SELF_TEST_CASES = [
    ("【同花顺】您的委托已成交：买入 600519 贵州茅台 100股 成交价1500.00元",
     "BUY", "600519", 100, 1500.0),
    ("证券名称：工商银行 证券代码：601398 买卖标志：买入 成交数量：500 成交价格：5.10",
     "BUY", "601398", 500, 5.10),
    ("平安证券：您的卖出委托已成交 平安银行（000001）200股 12.50元",
     "SELL", "000001", 200, 12.5),
    ("您卖出的 中国平安 601318 已成交 300股 价格 45.80元",
     "SELL", "601318", 300, 45.8),
    ("【同花顺】您的买入委托已成交：创业板ETF(159915) 1手 1.85元",
     "BUY", "159915", 100, 1.85),
    ("您的基金申购已确认 510300 沪深300ETF 500份 净值 3.90",
     "BUY", "510300", 500, 3.9),
    # 非成交消息（应返回 None）
    ("您好，您关注的贵州茅台最新研报已发布，点击查看详情",
     None, None, 0, 0),
    ("【同花顺】早盘三大指数集体高开，沪指涨0.3%",
     None, None, 0, 0),
]

