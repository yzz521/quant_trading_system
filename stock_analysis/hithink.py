"""同花顺金融数据服务（hithink finance）A 股数据源适配器。

覆盖范围
--------
只替换 **A 股** 的行情 / 估值 / 财务取数路径。港股与美股沿用现有数据源
（akshare / yfinance / 腾讯接口），本模块不做任何改动。

为什么走 REST 而不是官方 Python SDK
------------------------------------
1. **三端打包**：本项目用 PyInstaller 打 Windows / macOS / Linux 包。官方 SDK
   会引入 duckdb + pyarrow 等重型依赖，显著抬升包体与启动期内存峰值——本项目
   历史上出现过 frozen 模式内存分配失败。REST 方案零新增依赖，只用标准库
   urllib。
2. **许可证**：官方仓库根目录是 MIT，但 ``python/pyproject.toml`` 中 marketdb
   包的 license 声明为 ``Proprietary``。桌面端打包属于代码再分发，风险较高；
   仅通过 HTTPS 调用接口、不引入其代码，不受该声明约束。
3. **线程安全**：urllib 无全局状态，可直接用于批量扫描（akshare 非线程安全，
   现有代码已因此改用新浪 / 腾讯接口）。

失败策略
--------
任何异常都返回 ``None`` / 空 DataFrame，调用方回退到原有数据源；鉴权失败会
短暂熔断，避免批量扫描时反复重试拖慢整体流程。

API Key 配置（按优先级）
------------------------
1. 环境变量 ``HITHINK_FINANCE_API_KEY``（推荐，三端通用）
2. ``<数据目录>/hithink.env``，内容 ``HITHINK_FINANCE_API_KEY=xxx``
3. ``<数据目录>/secret.local.yaml`` 的 ``hithink_api_key`` 字段

数据目录：优先 ``QTS_DATA_DIR`` 环境变量（桌面端打包后指向 exe 旁 config/），
否则回退到仓库根 config/。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

from ..utils import get_logger

log = get_logger("HiThink")

BASE_URL = os.environ.get("HITHINK_FINANCE_BASE_URL", "https://fuyao.aicubes.cn").rstrip("/")
_TIMEOUT = 15
_RETRIES = 2

# fuyao.aicubes.cn 是国内服务，环境里的 HTTP(S)_PROXY 会导致请求失败或被
# 中间设备拦截。这里固定用禁用代理的 opener，与模块调用方解耦（现有代码对
# 新浪 / 腾讯等国内源也做了同样的清代理处理）。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# --------------------------------------------------------------------------- #
# API Key 解析（带短缓存，避免批量扫描时反复读盘）
# --------------------------------------------------------------------------- #
_key_cache: dict = {"value": "", "checked": 0.0}
_state: dict = {"blocked_until": 0.0}


def _config_dir() -> Path:
    """数据目录：桌面端打包后由 QTS_DATA_DIR 指向 exe 旁 config/。"""
    env = os.environ.get("QTS_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "config"


def _resolve_key() -> str:
    key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if key:
        return key
    cfg = _config_dir()
    env_file = cfg / "hithink.env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "HITHINK_FINANCE_API_KEY" and v.strip():
                    return v.strip()
        except OSError:
            pass
    secret = cfg / "secret.local.yaml"
    if secret.is_file():
        try:
            from ..utils import load_yaml

            data = load_yaml(secret) or {}
            if isinstance(data, dict):
                val = str(data.get("hithink_api_key") or "").strip()
                if val:
                    return val
        except Exception:  # noqa: BLE001
            pass
    return ""


def _api_key() -> str:
    now = time.time()
    if _key_cache["value"] and now - _key_cache["checked"] < 60:
        return _key_cache["value"]
    key = _resolve_key()
    _key_cache["value"] = key
    _key_cache["checked"] = now
    return key


_warned: dict = {"no_key": False}


def is_enabled() -> bool:
    """是否可用：已配置 Key 且未处于鉴权熔断期。"""
    if not _api_key():
        # 只提示一次。把安装包发给别人时，未配置 Key 的一方也能在日志里确认
        # "A 股走的是旧数据源"，而不会误以为新源已生效。
        if not _warned["no_key"]:
            _warned["no_key"] = True
            log.info("未配置同花顺 API Key（环境变量 HITHINK_FINANCE_API_KEY 或 "
                     "config/hithink.env），A 股回退旧数据源")
        return False
    return time.time() >= _state["blocked_until"]


def _block(seconds: float = 300.0) -> None:
    """鉴权失败熔断，避免批量扫描时反复重试。"""
    if _state["blocked_until"] < time.time() + seconds:
        _state["blocked_until"] = time.time() + seconds
        log.warning("同花顺接口鉴权失败，%.0f 秒内跳过该数据源（回退旧源）", seconds)


# --------------------------------------------------------------------------- #
# Key 管理（供设置页调用）
# --------------------------------------------------------------------------- #
def masked_key() -> str:
    """已配置 Key 的脱敏展示（前 6 后 4），未配置返回空串。"""
    key = _api_key()
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}****{key[-4:]}"


def save_api_key(key: str) -> bool:
    """写入 ``<数据目录>/hithink.env`` 并让新 Key 立即生效。

    文件权限收窄为 600（Unix），避免同机其他账号读取；Windows 无此语义则忽略。
    """
    key = (key or "").strip()
    if not key:
        return False
    path = _config_dir() / "hithink.env"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# 同花顺金融数据服务 API Key（本地生成，勿提交 git / 勿随包分发）\n"
            f"HITHINK_FINANCE_API_KEY={key}\n",
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:  # noqa: BLE001
        log.warning("保存同花顺 API Key 失败: %s", e)
        return False
    # 立即生效：刷新缓存、解除可能存在的熔断
    _key_cache["value"] = key
    _key_cache["checked"] = time.time()
    _state["blocked_until"] = 0.0
    _warned["no_key"] = False
    return True


def clear_api_key() -> bool:
    """删除本地 Key，回到未配置状态（A 股回退旧数据源）。"""
    path = _config_dir() / "hithink.env"
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:  # noqa: BLE001
        log.warning("删除同花顺 API Key 文件失败: %s", e)
        return False
    _key_cache["value"] = ""
    _key_cache["checked"] = 0.0
    _state["blocked_until"] = 0.0
    return True


def test_connection() -> tuple[bool, str]:
    """用最小请求验证 Key 是否可用，返回 ``(是否可用, 说明)``。"""
    if not _api_key():
        return False, "未配置 API Key"
    _state["blocked_until"] = 0.0  # 测试时忽略既有熔断
    data = _get("/api/a-share/prices/snapshot", {"thscodes": "600519.SH"})
    if data is None:
        return False, "Key 无效或请求失败（A 股将回退旧数据源）"
    items = data.get("item") or []
    if not items:
        return False, "接口正常但无数据返回"
    return True, f"连接正常（样例 {items[0].get('ticker', '600519')} 已返回行情）"


# --------------------------------------------------------------------------- #
# HTTP 基础设施
# --------------------------------------------------------------------------- #
def _get(path: str, params: dict) -> Optional[dict]:
    """GET 一个 REST 端点，返回 ``data`` 字典；失败返回 None。"""
    if not is_enabled():
        return None
    url = f"{BASE_URL}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"X-api-key": _api_key(), "User-Agent": "Mozilla/5.0"},
    )
    payload = None
    for attempt in range(_RETRIES + 1):
        try:
            with _NO_PROXY_OPENER.open(req, timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                _block()
                return None
            if attempt == _RETRIES:
                log.debug("同花顺请求失败 %s: HTTP %s", path, e.code)
                return None
            time.sleep(0.3 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            if attempt == _RETRIES:
                log.debug("同花顺请求失败 %s: %s", path, e)
                return None
            time.sleep(0.3 * (attempt + 1))
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if data is None:
        # 鉴权失败既可能是 HTTP 401/403（上面已处理），也可能是 HTTP 200 包裹的
        # 业务码 2003（"Invalid or revoked API key"）。两种情况都必须熔断，
        # 否则批量扫描几千只票会逐只重试，把整轮拖垮。
        code = payload.get("code")
        msg = str(payload.get("message") or "")
        auth_failed = (
            code in (2003, "2003")
            or any(w in msg.lower() for w in ("invalid", "revoked", "unauthorized"))
        )
        if auth_failed:
            _block()
            return None
        log.debug("同花顺 %s 无 data: code=%s msg=%s", path, code, msg)
        return None
    return data


def _ms(dt) -> int:
    """任意日期 → 毫秒时间戳（接口统一用毫秒）。"""
    return int(pd.Timestamp(dt).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# 代码转换
# --------------------------------------------------------------------------- #
def to_thscode(code: str) -> Optional[str]:
    """6 位 A 股代码 → thscode（600519.SH / 000001.SZ / 830799.BJ）。

    已是带后缀的 thscode 则原样返回；非法返回 None。
    """
    s = str(code).strip().upper()
    if "." in s:
        return s
    if not (s.isdigit() and len(s) == 6):
        return None
    head = s[0]
    if head in ("6", "5", "9"):      # 沪市股票 / 沪市 ETF（51、56、58）
        return s + ".SH"
    if head in ("0", "1", "2", "3"):  # 深市股票 / 深市 ETF（159、16x、18x）
        return s + ".SZ"
    if head in ("4", "8"):           # 北交所
        return s + ".BJ"
    return None


def is_etf(code: str) -> bool:
    """场内基金（ETF / LOF）：与 detect_market 口径一致，5 与 1 开头。"""
    s = str(code).strip()
    return s.startswith(("5", "1"))


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------------------- #
# 历史日 K
# --------------------------------------------------------------------------- #
def _bars_to_frame(items: list) -> pd.DataFrame:
    """K 线 item 列表 → open/high/low/close/volume + DatetimeIndex。"""
    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items)
    if "date_ms" not in df.columns:
        return pd.DataFrame()
    df = df.rename(columns={
        "open_price": "open",
        "high_price": "high",
        "low_price": "low",
        "close_price": "close",
    })
    # date_ms 是"北京时间当日 00:00"对应的 Unix 毫秒。直接 to_datetime(unit="ms")
    # 会按 UTC 解析，显示为前一日 16:00，导致日线整体错位一天（下游 .date() 取到
    # 昨天）。这里补 8 小时对齐北京时间，与 akshare / 新浪路径的 naive 日期口径
    # 一致。不依赖 zoneinfo，避免打包时缺 tzdata。
    df["datetime"] = pd.to_datetime(
        pd.to_numeric(df["date_ms"], errors="coerce") + 8 * 3600 * 1000, unit="ms"
    )
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = float("nan")
    df = df.set_index("datetime").sort_index()
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_kline_cn(info, days: int = 250, adjust: str = "forward") -> pd.DataFrame:
    """A 股 / 场内 ETF 日 K（前复权口径，与 akshare ``adjust="qfq"`` 一致）。

    参数 ``info`` 为 ``MarketInfo``（由 ``detect_market`` 生成）。失败返回空
    DataFrame，调用方回退到原有数据源。
    """
    if not is_enabled():
        return pd.DataFrame()
    ths = to_thscode(info.code)
    if not ths:
        return pd.DataFrame()

    end_ms = _ms(pd.Timestamp.today())
    span = days + 60  # 额外缓冲，覆盖休市
    if is_etf(info.code):
        # ETF 历史窗口上限 5 年，且不支持 adjust 参数
        span = min(span, 1800)
        path = "/api/fund/market/historical"
        params = {"thscode": ths, "interval": "1d",
                  "start": _ms(pd.Timestamp.today() - pd.Timedelta(days=span)),
                  "end": end_ms}
    else:
        path = "/api/a-share/prices/historical"
        params = {"thscode": ths, "interval": "1d",
                  "start": _ms(pd.Timestamp.today() - pd.Timedelta(days=span)),
                  "end": end_ms, "adjust": adjust}
    data = _get(path, params)
    if not data:
        return pd.DataFrame()
    df = _bars_to_frame(data.get("item") or [])
    if df.empty:
        return df
    return df.tail(days)


# --------------------------------------------------------------------------- #
# 最新快照
# --------------------------------------------------------------------------- #
def fetch_snapshot_cn(codes: list[str]) -> Optional[pd.DataFrame]:
    """批量 A 股最新行情快照（每批最多 100 个 thscode）。

    返回列：code / close / pct_chg / open / high / low / prev_close /
    volume / amount。注意接口不返回中文名。
    """
    if not is_enabled() or not codes:
        return None
    rows: list[dict] = []
    for chunk in _batches(codes, 100):
        ths_list = [t for t in (to_thscode(c) for c in chunk) if t]
        if not ths_list:
            continue
        data = _get("/api/a-share/prices/snapshot",
                    {"thscodes": ",".join(ths_list)})
        if not data:
            continue
        for it in data.get("item") or []:
            rows.append({
                "code": str(it.get("ticker") or "").zfill(6),
                "close": it.get("last_price"),
                "pct_chg": it.get("price_change_ratio_pct"),
                "open": it.get("open_price"),
                "high": it.get("high_price"),
                "low": it.get("low_price"),
                "prev_close": it.get("prev_price"),
                "volume": it.get("volume"),
                "amount": it.get("turnover"),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ("close", "pct_chg", "open", "high", "low",
                "prev_close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# 估值快照
# --------------------------------------------------------------------------- #
def fetch_valuation_cn(codes: list[str]) -> Optional[pd.DataFrame]:
    """批量 A 股估值快照（PE_TTM / PE_MRQ / PB / PS / PCF）。

    返回含 ``pe_ttm`` 列，可与 akshare ``stock_a_indicator_lg`` 的结果互换消费。
    指标允许为 None 或负数（亏损 / 负现金流），调用方不应补零或取绝对值。
    """
    if not is_enabled() or not codes:
        return None
    rows: list[dict] = []
    for chunk in _batches(codes, 100):
        ths_list = [t for t in (to_thscode(c) for c in chunk) if t]
        if not ths_list:
            continue
        data = _get("/api/a-share/valuations/snapshot",
                    {"thscodes": ",".join(ths_list)})
        if not data:
            continue
        for it in data.get("item") or []:
            rows.append({
                "code": str(it.get("ticker") or "").zfill(6),
                "name": it.get("name"),
                "pe_ttm": it.get("pe_ttm"),
                "pe_mrq": it.get("pe_mrq"),
                "pb_mrq": it.get("pb_mrq"),
                "ps_ttm": it.get("ps_ttm"),
                "pcf_ttm": it.get("pcf_ttm"),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# 财务指标（成长因子）
# --------------------------------------------------------------------------- #
def _recent_reports(n: int = 4) -> list[str]:
    """从当前季度往前推 n 个报告期，格式 YYYY-[1-4]（最新在前）。"""
    today = pd.Timestamp.today()
    # 季报披露时点大致：Q1→4月底、Q2→8月底、Q3→10月底、Q4→次年4月底
    # 保守起见按"上一季"作为最新已披露期起点。
    out: list[str] = []
    year, quarter = today.year, (today.month - 1) // 3 + 1
    quarter -= 1  # 当季尚未披露完
    if quarter <= 0:
        year, quarter = year - 1, 4
    for _ in range(n):
        out.append(f"{year}-{quarter}")
        quarter -= 1
        if quarter <= 0:
            year, quarter = year - 1, 4
    return out


def _pick_growth(abilities: list) -> dict:
    """从 abilities 数组中按关键字提取营收 / 净利同比。

    ``abilities`` 是数组而非对象，每项形如 ``{ability, indicators:[{index_id,
    value}]}``。指标 id 由上游定义，这里按关键字模糊匹配，匹配不到就返回空，
    由调用方回退到 akshare，避免硬编码 id 失效后静默出错。
    """
    out: dict = {}
    for block in abilities or []:
        if not isinstance(block, dict) or block.get("ability") != "growth":
            continue
        for ind in block.get("indicators") or []:
            idx = str(ind.get("index_id") or "").lower()
            val = ind.get("value")
            if val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            has_income = "operating_income" in idx or "revenue" in idx
            has_profit = "net_profit" in idx
            is_yoy = "yoy" in idx or "growth" in idx or "increase" in idx
            if not is_yoy:
                continue
            if has_income and "rev_yoy" not in out:
                out["rev_yoy"] = num
            elif has_profit and "profit_yoy" not in out:
                out["profit_yoy"] = num
    return out


def fetch_growth_cn(code: str) -> Optional[dict]:
    """A 股成长因子：``{"rev_yoy": ..., "profit_yoy": ...}``（百分比数值）。

    依次尝试最近几个报告期，取首个有数据的报告期。失败返回 None。
    """
    if not is_enabled():
        return None
    ths = to_thscode(code)
    if not ths:
        return None
    for report in _recent_reports():
        data = _get("/api/a-share/financials/indicators",
                    {"thscode": ths, "report": report})
        if not data:
            continue
        got = _pick_growth(data.get("abilities") or [])
        if got:
            return got
    return None
