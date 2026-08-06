"""Multi-channel notifier with a polished HTML template.

Channels: email (HTML), WeChat via Server酱 (text), Feishu bot (text).
``build_market_message`` produces both a plain-text body (for IM channels)
and a styled HTML body (for email) including a holdings-PnL block, a
diagnosis block, a scan-hit block and a risk block — one message per market.
"""
from __future__ import annotations

import hmac
import hashlib
import base64
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import requests

from ..utils import get_logger, load_yaml

log = get_logger("Notifier")

UP = "#d62728"      # red  — gains (CN convention)
DOWN = "#2ca02c"    # green — losses
NEUTRAL = "#6b7280"

RATING_COLOR = {
    "强烈买入": UP, "买入": UP, "观望": NEUTRAL,
    "减持": DOWN, "卖出": DOWN,
}


def _chg_color(val: float) -> str:
    if val is None:
        return NEUTRAL
    return UP if val >= 0 else DOWN


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


class Notifier:
    def __init__(self, config_path: str = "config/notify.yaml") -> None:
        self.cfg = load_yaml(config_path) or {}
        self._notify_cfg = self.cfg.get("notify", {})
        self.channels = [n for n, c in self._notify_cfg.items()
                         if isinstance(c, dict) and c.get("enabled")]
        log.info("通知渠道已启用: %s", self.channels or "无")

    # ------------------------------------------------------------------ #
    def send(self, title: str, text: str, html: Optional[str] = None) -> dict:
        if not self.channels:
            log.info("无启用渠道，仅打印：\n%s\n%s", title, text)
            return {"_print": True}
        results = {}
        for ch in self.channels:
            try:
                if ch == "email":
                    self._send_email(title, text, html or text)
                elif ch == "serverchan":
                    self._send_serverchan(title, text)
                elif ch == "feishu":
                    self._send_feishu(title, text)
                results[ch] = "ok"
                log.info("推送成功 [%s] %s", ch, title)
            except Exception as e:  # noqa: BLE001
                results[ch] = f"fail: {e}"
                log.error("推送失败 [%s]: %s", ch, e)
        return results

    # ------------------------------------------------------------------ #
    def _send_email(self, title: str, text: str, html: str) -> None:
        c = self._notify_cfg["email"]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = f"{c.get('sender_name', 'GP助手')} <{c['username']}>"
        msg["To"] = ", ".join(c["to"])
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        if c.get("use_ssl", True):
            server = smtplib.SMTP_SSL(c["smtp_host"], int(c["smtp_port"]), timeout=30)
        else:
            server = smtplib.SMTP(c["smtp_host"], int(c["smtp_port"]), timeout=30)
            server.starttls()
        try:
            server.login(c["username"], c["password"])
            server.sendmail(c["username"], c["to"], msg.as_string())
        finally:
            server.quit()

    def _send_serverchan(self, title: str, content: str) -> None:
        c = self._notify_cfg["serverchan"]
        url = f"https://sctapi.ftqq.com/{c['sendkey']}.send"
        r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        if r.status_code != 200 or r.json().get("code", 0) != 0:
            raise RuntimeError(f"Server酱返回 {r.status_code}: {r.text[:100]}")

    def _send_feishu(self, title: str, content: str) -> None:
        c = self._notify_cfg["feishu"]
        text = f"【{title}】\n{content}"
        if len(text) > 28000:
            text = text[:28000] + "\n...(内容过长已截断)"
        payload = {"msg_type": "text", "content": {"text": text}}
        secret = c.get("secret", "")
        if secret:
            ts = str(int(time.time()))
            sign = base64.b64encode(
                hmac.new(f"{ts}\n{secret}".encode("utf-8"), digestmod=hashlib.sha256).digest()
            ).decode("utf-8")
            payload["timestamp"] = ts
            payload["sign"] = sign
        r = requests.post(c["webhook"], json=payload, timeout=15)
        if r.status_code != 200 or r.json().get("code", 0) != 0:
            raise RuntimeError(f"飞书返回 {r.status_code}: {r.text[:100]}")


# --------------------------------------------------------------------------- #
# Message builders — return (title, text_body, html_body)
# --------------------------------------------------------------------------- #
def build_market_message(market: str, diagnoses: list, scan_hits: Optional[list] = None,
                         scan_enabled: bool = True,
                         holdings: Optional[list] = None,
                         holdings_summary: Optional[dict] = None,
                         holding_actions: Optional[list] = None,
                         capital_snapshot: Optional[dict] = None,
                         ai_summary: Optional[str] = None,
                         funnel: Optional[dict] = None,
                         ) -> tuple[str, str, str]:
    now = time.strftime("%Y-%m-%d %H:%M")
    mname = {"CN": "A股", "US": "美股", "HK": "港股"}[market]
    title = f"GP助手 · {mname} {now}"

    text_parts: list[str] = []
    html_parts: list[str] = []

    # --- holdings block (first — it's what you care about most) ---
    if holdings:
        text_parts.append(f"== 我的{mname}持仓 ==")
        html_parts.append(_html_section(f"💼 我的{mname}持仓", _holdings_html(holdings, holdings_summary)))
        for h in holdings:
            pnl = h.get("pnl")
            pnlstr = "—" if pnl is None else f"{pnl:+.2f} ({_fmt_pct(h.get('pnl_pct'))})"
            text_parts.append(
                f"{h['code']} {h['name']} | {int(h['quantity'])}股 | 成本{h['cost_price']} | "
                f"现价{h.get('current_price','—')} | 盈亏 {pnlstr}"
            )
        if holdings_summary:
            s = holdings_summary
            text_parts.append(
                f"合计: 成本{s['total_cost']} 市值{s['total_value']} "
                f"盈亏{s['total_pnl']} ({_fmt_pct(s['total_pnl_pct'])}) "
                f"持{s['count']}只"
            )
            text_parts.append("")


    # --- capital summary ---
    if capital_snapshot:
        cs = capital_snapshot
        text_parts.append(
            f"== 资金账户 ==\n"
            f"总资金 {cs['total_capital']:,.0f} | 持仓占用(成本) {cs['invested_cost']:,.0f} | "
            f"可用 {cs['available_cash']:,.0f} | 单票上限 {cs['max_position_pct']:.0%} | "
            f"使用率 {cs['utilization_pct']}%\n"
        )
        html_parts.append(_html_section(
            "💰 资金账户",
            f"<p>总资金 <b>{cs['total_capital']:,.0f}</b>　"
            f"持仓占用(成本) <b>{cs['invested_cost']:,.0f}</b>　"
            f"可用 <b>{cs['available_cash']:,.0f}</b>　"
            f"单票上限 <b>{cs['max_position_pct']:.0%}</b>　"
            f"使用率 <b>{cs['utilization_pct']}%</b></p>"
            f"<p style='color:#6b7280;font-size:12px'>可用=总资金−持仓成本（忽略浮盈，偏保守）。"
            f"满仓时「可买」常为空属正常。</p>",
        ))


    # --- AI summary ---
    if ai_summary:
        text_parts.append("== GP助手 AI 点评 ==")
        text_parts.append(ai_summary.strip())
        text_parts.append("")
        html_ai = "<pre style='white-space:pre-wrap;font-family:inherit;line-height:1.55;padding:8px;margin:0'>" + (
            ai_summary.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ) + "</pre>"
        html_parts.append(_html_section("🤖 GP助手 AI 点评", html_ai))

    # --- holding action (sell / add) ---
    if holding_actions:
        from .holdings_action import actions_to_text, actions_to_html
        text_parts.append(actions_to_text(holding_actions))
        text_parts.append("")
        html_parts.append(_html_section("🎯 持仓卖出/加仓参考", actions_to_html(holding_actions)))

    # --- diagnosis block ---
    if diagnoses:
        text_parts.append(f"== {mname}自选股诊断 ==")
        html_parts.append(_html_section(f"📊 {mname}自选股诊断", _diagnoses_html(diagnoses)))
        for d in diagnoses:
            signals = "/".join(s["name"] for s in d.get("signals", [])) or "无信号"
            adv = d.get("advice", {}) or {}
            buy, stop, take = adv.get("buy_price"), adv.get("stop_loss"), adv.get("take_profit")
            advice_str = (f" | 买{buy}/止损{stop}/止盈{take}" if buy
                          else (f" | 离场位{stop}" if stop else ""))
            tag = d.get("buy_label") or ""
            text_parts.append(
                f"{d['code']} {d['name']} | 评分{d['score']} {d['rating']} | "
                f"{d['trend']} | {d['price']} ({_fmt_pct(d['change_pct'])}) | {signals}{advice_str}"
                + (f" | {tag}" if tag else "")
            )

    # --- scan block (with optional buy-power split) ---
    if funnel and funnel.get("hits"):
        text_parts.append("")
        stages = funnel.get("stages", [])
        stats = " → ".join(str(s.get("after")) for s in stages)
        text_parts.append(
            f"== 收盘漏斗 Top{len(funnel['hits'])}（全市场 {funnel.get('total', '?')} 只 → {stats}） =="
        )
        for h in funnel["hits"]:
            mf = f" 主力{_fmt_amount(h.get('main_net'))}" if h.get("main_net") is not None else ""
            text_parts.append(
                f"{h['code']} {h['name']} | {h['close']} ({_fmt_pct(h.get('change_pct'))}) | "
                f"评分{h['score']} | 市值{h.get('market_cap')}亿 PE{h.get('pe')} "
                f"换手{h.get('turnover')}%{mf} | {_fmt_matched(h)}"
                + (f" | {h.get('buy_label')}" if h.get("buy_label") else "")
            )
        html_parts.append(_html_section("🔻 收盘漏斗关注池", _funnel_html(funnel)))
    elif scan_enabled and scan_hits:
        text_parts.append("")
        has_tags = any(h.get("buy_tag") for h in scan_hits)
        if has_tags:
            from .buy_power import partition_annotated
            parts = partition_annotated(scan_hits, max_ok=10)
            def _scan_line(h):
                tag = h.get("buy_label") or ""
                matched = _fmt_matched(h)
                return (
                    f"{h.get('code')} {h.get('name')} | {h.get('close')} "
                    f"({_fmt_pct(h.get('change_pct'))}) | 评分{h.get('score')} | {matched}"
                    + (f" | {tag}" if tag else "")
                )
            text_parts.append(f"== 新开仓·可买（最多10只，共扫描 {len(scan_hits)}） ==")
            if parts["ok"]:
                for h in parts["ok"]:
                    text_parts.append(_scan_line(h))
            else:
                text_parts.append("（当前可用资金下无「可买」标的，满仓时属正常）")
            rest = parts["no_cash"] + parts["capped"] + parts["held"] + parts["other"] + parts["ok_extra"]
            if rest:
                text_parts.append("")
                text_parts.append(f"== 新开仓·资金不足/已持有/其他（{len(rest)} 只） ==")
                for h in rest[:40]:
                    text_parts.append(_scan_line(h))
            html_parts.append(_html_section(
                "🔍 扫描命中（含可买性）",
                _scan_html_annotated(scan_hits),
            ))
        else:
            text_parts.append(f"== 扫描命中 {len(scan_hits)} 只 ==")
            html_parts.append(_html_section(f"🔍 扫描命中 {len(scan_hits)} 只", _scan_html(scan_hits)))
            for h in scan_hits[:30]:
                text_parts.append(
                    f"{h['code']} {h['name']} | {h['close']} ({_fmt_pct(h['change_pct'])}) | "
                    f"评分{h['score']} | {', '.join(h['matched'])}"
                )

    # --- risk block ---
    risks = []
    for d in diagnoses:
        stock = f"{d['name']}({d['code']})"
        for r in d.get("risks", []):
            if "暂未触发" not in r:
                risks.append({"stock": stock, "risk": r})
    if risks:
        text_parts.append("")
        text_parts.append("== 风险提示 ==")
        html_parts.append(_html_section("⚠️ 风险提示", _risks_html(risks)))
        for rk in risks[:12]:
            text_parts.append(f"⚠️ {rk['stock']}: {rk['risk']}")

    text_body = "\n".join(text_parts)
    html_body = _html_wrap(title, mname, html_parts)
    return title, text_body, html_body


# --------------------------------------------------------------------------- #
# HTML helpers
# --------------------------------------------------------------------------- #
def _html_wrap(title: str, mname: str, sections: list[str]) -> str:
    bar_color = {"A股": "#c0392b", "美股": "#2c3e50", "港股": "#8e44ad"}.get(mname, "#1f77b4")
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,"PingFang SC","Segoe UI",sans-serif;background:#f4f5f7;margin:0;padding:20px;}}
.card{{max-width:680px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.08);}}
.bar{{background:{bar_color};color:#fff;padding:18px 24px;}}
.bar h1{{margin:0;font-size:17px;font-weight:500;}}
.bar .sub{{font-size:12px;opacity:.85;margin-top:4px;}}
.sec{{padding:14px 20px 6px;}}
.sec h2{{font-size:14px;margin:0 0 10px;color:#1f2933;border-left:3px solid {bar_color};padding-left:8px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{background:#f0f4f8;color:#374151;font-weight:600;padding:7px 8px;text-align:left;border-bottom:1px solid #e5e7eb;}}
td{{padding:6px 8px;border-bottom:1px solid #f0f0f0;}}
tr:hover td{{background:#fafbfc;}}
.up{{color:{UP};font-weight:600;}} .down{{color:{DOWN};font-weight:600;}} .neutral{{color:{NEUTRAL};}}
.tag{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;color:#fff;}}
.foot{{padding:12px 24px;background:#f7f8fa;font-size:11px;color:#9ca3af;text-align:center;border-top:1px solid #e5e7eb;}}
</style></head><body>
<div class="card">
  <div class="bar"><h1>{title}</h1><div class="sub">GP助手 · 开盘期间定时推送</div></div>
  {''.join(sections)}
  <div class="foot">本邮件由量化系统自动发送，内容仅供参考，不构成投资建议 · 投资有风险，决策需谨慎</div>
</div>
</body></html>"""


def _html_section(heading: str, table_html: str) -> str:
    return f'<div class="sec"><h2>{heading}</h2>{table_html}</div>'


def _fmt_matched(h: dict) -> str:
    """命中条件文案：新晋条件标“新晋”，持续条件标“持续N天”。"""
    parts = []
    for m in h.get("matched") or []:
        days = (h.get("matched_days") or {}).get(m)
        if days == 1:
            parts.append(f"{m}·新晋")
        elif days and days > 1:
            parts.append(f"{m}·持续{days}天")
        else:
            parts.append(m)
    return "、".join(parts)


def _fmt_amount(v) -> str:
    """金额格式化：亿/万/元。"""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"


def _funnel_html(funnel: dict) -> str:
    """收盘漏斗关注池表格：市值/PE/换手/主力净流入/命中/可买性。"""
    stages = funnel.get("stages", [])
    stats = " → ".join(str(s.get("after")) for s in stages)
    head = (
        f"<p style='color:#6b7280;font-size:12px;padding:6px 0'>"
        f"漏斗：{funnel.get('total', '?')} 只 → {stats}，耗时 {funnel.get('elapsed', '-')}s</p>"
    )
    rows = ""
    for h in funnel.get("hits", [])[:20]:
        ccls = "up" if (h.get("change_pct") or 0) >= 0 else "down"
        mf = _fmt_amount(h.get("main_net"))
        tag = h.get("buy_label") or ""
        rows += (
            f"<tr><td>{h.get('code')}</td><td>{h.get('name')}</td>"
            f"<td>{h.get('close')}</td><td class='{ccls}'>{_fmt_pct(h.get('change_pct'))}</td>"
            f"<td><span class='tag' style='background:#1f77b4'>{h.get('score')}</span></td>"
            f"<td>{h.get('market_cap') if h.get('market_cap') is not None else '—'}</td>"
            f"<td>{h.get('pe') if h.get('pe') is not None else '—'}</td>"
            f"<td>{h.get('turnover') if h.get('turnover') is not None else '—'}</td>"
            f"<td>{mf}</td><td style='font-size:12px'>{_fmt_matched(h)}</td>"
            f"<td style='font-size:12px'>{tag}</td></tr>"
        )
    return head + (
        "<table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th>"
        "<th>评分</th><th>市值(亿)</th><th>PE</th><th>换手%</th>"
        "<th>主力净流入</th><th>命中</th><th>可买性</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _holdings_html(holdings: list, summary: Optional[dict]) -> str:
    rows = ""
    for h in holdings:
        pnl = h.get("pnl")
        cls = "up" if (pnl is not None and pnl >= 0) else "down" if pnl is not None else "neutral"
        pnl_str = "—" if pnl is None else f"{pnl:+,.2f}"
        pct_str = "—" if h.get("pnl_pct") is None else _fmt_pct(h["pnl_pct"])
        rows += (
            f"<tr><td>{h['code']}</td><td>{h['name']}</td>"
            f"<td>{int(h['quantity'])}</td><td>{h['cost_price']}</td>"
            f"<td>{h.get('current_price','—')}</td>"
            f"<td class='{cls}'>{pnl_str}</td><td class='{cls}'>{pct_str}</td></tr>"
        )
    srow = ""
    if summary:
        cls = "up" if summary["total_pnl"] >= 0 else "down"
        srow = (
            f"<tr style='background:#fff8e1;font-weight:600'><td colspan='5'>合计 "
            f"({summary['count']}只 · 成本{summary['total_cost']:,.0f} · 市值{summary['total_value']:,.0f})</td>"
            f"<td class='{cls}'>{summary['total_pnl']:+,.2f}</td>"
            f"<td class='{cls}'>{_fmt_pct(summary['total_pnl_pct'])}</td></tr>"
        )
    return (
        "<table><thead><tr><th>代码</th><th>名称</th><th>持仓</th><th>成本</th>"
        "<th>现价</th><th>盈亏</th><th>盈亏%</th></tr></thead>"
        f"<tbody>{rows}{srow}</tbody></table>"
    )


def _diagnoses_html(diagnoses: list) -> str:
    if not diagnoses:
        return "<p class='neutral' style='padding:8px'>暂无数据</p>"
    rows = ""
    for d in diagnoses:
        rcolor = RATING_COLOR.get(d["rating"], NEUTRAL)
        ccls = "up" if d["change_pct"] >= 0 else "down"
        signals = "、".join(s["name"] for s in d.get("signals", [])) or "无"
        adv = d.get("advice", {}) or {}
        buy, stop, take = adv.get("buy_price"), adv.get("stop_loss"), adv.get("take_profit")
        if buy:
            advice_cell = (f"<td style='font-size:11px;line-height:1.5'>"
                           f"买 <span class='up'>{buy}</span><br>"
                           f"损 <span class='down'>{stop}</span><br>"
                           f"盈 <span class='up'>{take}</span></td>")
        elif stop:
            advice_cell = f"<td style='font-size:11px'><span class='down'>离场 {stop}</span></td>"
        else:
            advice_cell = "<td class='neutral'>—</td>"
        rows += (
            f"<tr><td>{d['code']}</td><td>{d['name']}</td>"
            f"<td><span class='tag' style='background:{rcolor}'>{d['score']}</span></td>"
            f"<td style='color:{rcolor}'>{d['rating']}</td><td>{d['trend']}</td>"
            f"<td>{d['price']}</td><td class='{ccls}'>{_fmt_pct(d['change_pct'])}</td>"
            f"<td style='font-size:12px;color:#6b7280'>{signals}</td>{advice_cell}</tr>"
        )
    return (
        "<table><thead><tr><th>代码</th><th>名称</th><th>评分</th><th>评级</th>"
        "<th>趋势</th><th>现价</th><th>涨跌</th><th>信号</th><th>建议价位</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _scan_html(hits: list) -> str:
    rows = ""
    for h in hits[:30]:
        ccls = "up" if h["change_pct"] >= 0 else "down"
        matched = _fmt_matched(h)
        rows += (
            f"<tr><td>{h['code']}</td><td>{h['name']}</td><td>{h['close']}</td>"
            f"<td class='{ccls}'>{_fmt_pct(h['change_pct'])}</td>"
            f"<td><span class='tag' style='background:#1f77b4'>{h['score']}</span></td>"
            f"<td style='font-size:12px'>{matched}</td></tr>"
        )
    extra = f"<p class='neutral' style='font-size:12px;padding:6px'>共{len(hits)}只，已显示前30</p>" if len(hits) > 30 else ""
    return (
        "<table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th>"
        "<th>评分</th><th>匹配条件</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{extra}"
    )


def _risks_html(risks: list) -> str:
    items = "".join(
        f"<li style='color:{DOWN};margin:4px 0'>⚠️ <b>{r['stock']}</b>: {r['risk']}</li>"
        for r in risks[:12]
    )
    return f"<ul style='padding-left:20px;margin:6px 0'>{items}</ul>"


def _scan_html_annotated(hits: list) -> str:
    if not hits:
        return "<p class='neutral'>暂无</p>"
    rows = ""
    for h in hits[:50]:
        tag = h.get("buy_label") or ""
        matched = _fmt_matched(h)
        rows += (
            f"<tr><td>{h.get('code')}</td><td>{h.get('name')}</td>"
            f"<td>{h.get('close')}</td><td>{_fmt_pct(h.get('change_pct'))}</td>"
            f"<td>{h.get('score')}</td><td>{matched}</td>"
            f"<td>{tag}</td></tr>"
        )
    return (
        "<table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th>"
        "<th>评分</th><th>命中</th><th>可买性</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
