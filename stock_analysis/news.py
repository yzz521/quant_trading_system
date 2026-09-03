"""Information layer — company notices + headlines, one vote into risk.

Live paths may fetch Eastmoney 公告/新闻. Backtests must keep ``fetch_news=False``
so today's headlines are not applied to historical bars (look-ahead).

Scoring rule (same as technical oscillators): many headlines, one vote.
Duplicate 减持 articles count as one risk keyword, not N penalties.
"""
from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..utils import get_logger
from .data_fetcher import _clear_proxy, detect_market

log = get_logger("News")

_CACHE: dict[str, tuple[float, "InformationSnapshot"]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 6 * 3600
_LOOKBACK_DAYS = 14

# Severe: 监管/存续风险。命中后额外降分并在计划里标红，不单独改 BUY/AVOID。
SEVERE_KEYWORDS = (
    "立案", "调查", "处罚", "违规", "冻结", "退市", "造假", "破产", "*ST", "被实施其他风险警示",
)
RISK_KEYWORDS = (
    "诉讼", "仲裁", "问询", "减持", "质押", "预亏", "业绩亏损", "大幅亏损",
    "非标", "占用资金", "爆仓", "停产", "业绩变脸", "立案", "调查", "处罚",
    "违规", "冻结", "退市", "造假", "破产",
)
CATALYST_KEYWORDS = (
    "回购", "增持", "中标", "签约", "重大合同", "股权激励", "预增", "超预期",
    "获批", "专利", "纳入", "高送转", "扭亏", "业绩增长",
)

_SPACE_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE_RE.sub("", str(text or ""))


def _parse_dt(raw: str) -> Optional[datetime]:
    s = str(raw or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def classify_title(title: str) -> tuple[str, Optional[str], str]:
    """Return (kind, keyword, severity). Risk wins if both risk and catalyst hit."""
    t = _norm(title)
    if not t:
        return "neutral", None, "none"
    for kw in SEVERE_KEYWORDS:
        if kw in t:
            return "risk", kw, "severe"
    for kw in RISK_KEYWORDS:
        if kw in t:
            return "risk", kw, "medium"
    for kw in CATALYST_KEYWORDS:
        if kw in t:
            return "catalyst", kw, "pos"
    return "neutral", None, "none"


@dataclass
class InformationSnapshot:
    """One information vote for a symbol (0-100, 50 = no news / neutral)."""

    score: float = 50.0
    grade: str = "中性"  # 风险 / 偏空 / 中性 / 偏多
    tags: list[str] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    catalysts: list[dict] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    severe: bool = False

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "grade": self.grade,
            "tags": self.tags,
            "risks": self.risks,
            "catalysts": self.catalysts,
            "headlines": self.headlines,
            "severe": self.severe,
        }


def rate_headlines(items: list[dict]) -> InformationSnapshot:
    """Classify already-fetched items. Empty list → neutral 50 (does not move scores)."""
    if not items:
        return InformationSnapshot()
    risks: list[dict] = []
    catalysts: list[dict] = []
    seen_risk: set[str] = set()
    seen_cat: set[str] = set()
    headlines: list[str] = []
    severe = False
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        if len(headlines) < 5:
            headlines.append(title)
        kind, kw, sev = classify_title(title)
        row = {
            "title": title,
            "source": it.get("source") or "",
            "date": it.get("date") or "",
            "keyword": kw,
            "severity": sev,
        }
        if kind == "risk" and kw and kw not in seen_risk:
            seen_risk.add(kw)
            risks.append(row)
            if sev == "severe":
                severe = True
        elif kind == "catalyst" and kw and kw not in seen_cat:
            seen_cat.add(kw)
            catalysts.append(row)

    score = 50.0
    score -= min(40.0, 10.0 * len(risks))
    if severe:
        score -= 10.0
    score += min(20.0, 10.0 * len(catalysts))
    score = float(max(0.0, min(100.0, score)))
    if severe or score <= 35:
        grade = "风险"
    elif score < 48:
        grade = "偏空"
    elif score >= 60:
        grade = "偏多"
    else:
        grade = "中性"
    tags: list[str] = []
    if severe:
        tags.append("重大风险")
    tags.extend(sorted(seen_risk))
    tags.extend(sorted(seen_cat))
    return InformationSnapshot(
        score=round(score, 1),
        grade=grade,
        tags=tags[:8],
        risks=risks,
        catalysts=catalysts,
        headlines=headlines,
        severe=severe,
    )


def _http_get(url: str, *, referer: str, timeout: float = 8.0) -> str:
    _clear_proxy()
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
            "Accept": "*/*",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout, context=ctx).read().decode("utf-8", "ignore")


def _cutoff() -> datetime:
    return datetime.now() - timedelta(days=_LOOKBACK_DAYS)


def fetch_announcements(code: str, limit: int = 15) -> list[dict]:
    """Eastmoney 法定公告（urllib，线程安全）。失败返回 []."""
    code = str(code).zfill(6)
    url = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?sr=-1&page_size={int(limit)}&page_index=1&ann_type=A"
        f"&client_source=web&stock_list={code}&f_node=0&s_node=0"
    )
    try:
        data = json.loads(_http_get(url, referer="https://data.eastmoney.com/"))
    except Exception as e:  # noqa: BLE001
        log.debug("公告拉取失败 %s: %s", code, e)
        return []
    rows = (((data or {}).get("data") or {}).get("list")) or []
    cut = _cutoff()
    out: list[dict] = []
    for r in rows:
        title = str(r.get("title") or r.get("title_ch") or "").strip()
        dt = _parse_dt(str(r.get("notice_date") or r.get("display_time") or ""))
        if dt is not None and dt < cut:
            continue
        if title:
            out.append({"title": title, "date": dt.strftime("%Y-%m-%d") if dt else "", "source": "公告"})
    return out


def fetch_news_headlines(code: str, limit: int = 8) -> list[dict]:
    """Eastmoney 个股新闻搜索（JSONP）。媒体稿噪声大，只作关键词命中补充。"""
    inner = {
        "uid": "",
        "keyword": str(code),
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": int(limit),
                "preTag": " ",
                "postTag": " ",
            }
        },
    }
    qs = urllib.parse.urlencode({"cb": "jQuery", "param": json.dumps(inner, ensure_ascii=False)})
    url = "https://search-api-web.eastmoney.com/search/jsonp?" + qs
    try:
        raw = _http_get(url, referer=f"https://so.eastmoney.com/news/s?keyword={code}")
    except Exception as e:  # noqa: BLE001
        log.debug("新闻拉取失败 %s: %s", code, e)
        return []
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start + 1 : end])
    except json.JSONDecodeError:
        return []
    arts = ((data.get("result") or {}).get("cmsArticleWebOld")) or []
    cut = _cutoff()
    out: list[dict] = []
    for r in arts:
        title = _norm(str(r.get("title") or ""))
        dt = _parse_dt(str(r.get("date") or ""))
        if dt is not None and dt < cut:
            continue
        if title:
            out.append({
                "title": title,
                "date": dt.strftime("%Y-%m-%d") if dt else "",
                "source": str(r.get("mediaName") or "新闻"),
            })
    return out


def fetch_and_rate(code: str, name: str = "") -> InformationSnapshot:
    """Fetch CN notices+news and score. Non-A-share or any failure → neutral 50."""
    info = detect_market(code)
    if info.market != "CN" or not info.code.isdigit() or len(info.code) != 6:
        return InformationSnapshot()
    key = info.code
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    items: list[dict] = []
    try:
        items.extend(fetch_announcements(key))
    except Exception as e:  # noqa: BLE001
        log.debug("公告层失败 %s: %s", key, e)
    try:
        items.extend(fetch_news_headlines(key))
    except Exception as e:  # noqa: BLE001
        log.debug("新闻层失败 %s: %s", key, e)
    snap = rate_headlines(items)
    log.debug("信息面 %s %s score=%s grade=%s", key, name, snap.score, snap.grade)
    with _CACHE_LOCK:
        _CACHE[key] = (now, snap)
    return snap
