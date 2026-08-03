"""公司行为提醒（分红/除权等）— 尽力拉取，失败则空列表，不阻断调度。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..utils import get_logger

log = get_logger("CorporateActions")


def fetch_upcoming_dividends(
    codes: list[str],
    *,
    within_days: int = 14,
) -> list[dict]:
    """Return upcoming dividend / record-date style events for given codes.

    Tries AkShare when available. On any failure returns [].
    Each item: code, name, event, date, info
    """
    if not codes:
        return []
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        log.info("akshare 未安装，跳过公司行为拉取")
        return []

    today = datetime.now().date()
    end = today + timedelta(days=within_days)
    out: list[dict] = []

    for code in codes:
        code = str(code).strip()
        if not code:
            continue
        try:
            # 历史分红明细（各版本列名可能不同，做宽松解析）
            df = None
            for fn_name in ("stock_history_dividend_detail", "stock_dividend_cninfo"):
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                try:
                    if fn_name == "stock_history_dividend_detail":
                        df = fn(symbol=code, indicator="分红")
                    else:
                        df = fn(symbol=code)
                    if df is not None and not df.empty:
                        break
                except Exception:  # noqa: BLE001
                    df = None
            if df is None or df.empty:
                continue

            # 找日期列
            date_col = None
            for c in df.columns:
                cs = str(c)
                if any(k in cs for k in ("股权登记", "除权", "除息", "公告", "日期")):
                    date_col = c
                    break
            if date_col is None:
                date_col = df.columns[0]

            for _, row in df.head(20).iterrows():
                raw = row.get(date_col)
                try:
                    d = pd_to_date(raw)
                except Exception:  # noqa: BLE001
                    continue
                if d is None or d < today or d > end:
                    continue
                info = " | ".join(f"{c}:{row[c]}" for c in list(df.columns)[:6])
                out.append({
                    "code": code,
                    "name": str(row.get("证券简称") or row.get("名称") or ""),
                    "event": "分红/除权相关",
                    "date": d.isoformat(),
                    "info": info[:200],
                })
        except Exception as e:  # noqa: BLE001
            log.debug("公司行为 %s 失败: %s", code, e)
            continue
    return out


def pd_to_date(raw):
    import pandas as pd
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    ts = pd.to_datetime(raw, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def corporate_actions_text(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["== 近期公司行为（分红/除权等，供参考） =="]
    for it in items:
        lines.append(
            f"{it.get('code')} {it.get('name','')} | {it.get('date')} | "
            f"{it.get('event')} | {it.get('info','')}"
        )
    return "\n".join(lines)


def corporate_actions_html(items: list[dict]) -> str:
    if not items:
        return ""
    rows = "".join(
        f"<tr><td>{it.get('code')}</td><td>{it.get('name','')}</td>"
        f"<td>{it.get('date')}</td><td>{it.get('info','')}</td></tr>"
        for it in items
    )
    return (
        "<table style='width:100%;font-size:13px;border-collapse:collapse'>"
        "<thead><tr style='background:#f3f4f6'><th>代码</th><th>名称</th>"
        "<th>日期</th><th>信息</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
        "<p style='color:#6b7280;font-size:12px'>日历类提醒，不构成买卖建议。</p>"
    )
