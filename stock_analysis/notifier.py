"""Multi-channel notifier with a polished HTML template.

Channels: email (HTML), WeChat via Server酱 (text), Feishu bot (text).
``build_market_message`` produces both a plain-text body (for IM channels)
and a styled HTML body (for email) including a holdings-PnL block, a
diagnosis block, a scan-hit block and a risk block — one message per market.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import smtplib
import time
from email.encoders import encode_base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    def send(
        self,
        title: str,
        text: str,
        html: Optional[str] = None,
        attachments: Optional[list[tuple[str, bytes]]] = None,
    ) -> dict:
        """attachments: [(filename, bytes)]，仅 email 渠道会附带。"""
        if not self.channels:
            log.info("无启用渠道，仅打印：\n%s\n%s", title, text)
            return {"_print": True}
        results = {}
        for ch in self.channels:
            try:
                if ch == "email":
                    self._send_email(title, text, html or text, attachments or [])
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
    def _send_email(
        self,
        title: str,
        text: str,
        html: str,
        attachments: Optional[list[tuple[str, bytes]]] = None,
    ) -> None:
        c = self._notify_cfg["email"]
        msg = MIMEMultipart()
        msg["Subject"] = title
        msg["From"] = f"{c.get('sender_name', 'GP助手')} <{c['username']}>"
        msg["To"] = ", ".join(c["to"])
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text, "plain", "utf-8"))
        alt.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(alt)
        for fname, payload in (attachments or []):
            part = MIMEApplication(payload, _subtype="pdf", _encoder=encode_base64)
            part.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(part)
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
def build_market_message(
    market: str,
    holdings: Optional[list] = None,
    holdings_summary: Optional[dict] = None,
    capital_snapshot: Optional[dict] = None,
    holding_quant: Optional[list] = None,
    holding_actions: Optional[list] = None,
    trading_plans: Optional[list] = None,
) -> tuple[str, str, str]:
    """构建每日决策邮件（持仓 / 资金 / 持仓量化 / 今日机会 / 卖出加仓参考）。"""
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

    # --- holdings quant (once per session day; already-held interpretation) ---
    if holding_quant:
        from .holdings_quant import quant_to_html, quant_to_text
        text_parts.append(quant_to_text(holding_quant))
        text_parts.append("")
        html_parts.append(_html_section("📐 持仓量化", quant_to_html(holding_quant)))

    # --- V2 trading plans (今日机会) ---
    if trading_plans:
        text_parts.append("== 🎯 今日机会 · 交易计划 ==")
        for p in trading_plans:
            d = p.get("decision", "")
            emoji = {"BUY_NOW": "🟢", "BUY_ON_PULLBACK": "🟢", "WATCH": "🟡",
                     "HOLD": "🟠", "SELL": "🔴", "AVOID": "⛔"}.get(d, "")
            line = (f"{emoji} {p.get('name')}({p.get('code')}) {d} | "
                    f"评分{p.get('stock_score')}/{p.get('opportunity_score')} | "
                    f"现价{p.get('current_price')} | 入场{p.get('entry_low')}~{p.get('entry_high')} | "
                    f"止损{p.get('stop_loss')} | 目标{p.get('target_1')}/{p.get('target_2')} | "
                    f"RR 1:{p.get('risk_reward_1')}")
            if p.get("position_percent") is not None:
                line += f" | 仓位{p.get('position_percent')}%"
            text_parts.append(line)
        text_parts.append("")
        html_parts.append(_html_section("🎯 今日机会 · 交易计划", _plans_html(trading_plans)))

    # --- holding action (sell / add) ---
    if holding_actions:
        from .holdings_action import actions_to_html, actions_to_text
        text_parts.append(actions_to_text(holding_actions))
        text_parts.append("")
        html_parts.append(_html_section("🎯 持仓卖出/加仓参考", actions_to_html(holding_actions)))

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
def _plans_html(plans: list) -> str:
    """V2 交易计划 HTML 表格（今日机会区块）。

    设计要点（窄屏邮件友好）：
      * 决策/代码/名称合并成一列「代码·名称」，节省宽度
      * 「风险收益」显示为 RR=N 而不是 1:N（更紧凑）
      * 「评分」去掉（机会分已隐含在决策/置信度里）
      * 表头用 8 列，< 600px 视窗下能完整显示
    """
    if not plans:
        return "<p class='neutral'>暂无</p>"

    def _p2(v) -> str:
        """价格两位小数；None/空 显示 —。"""
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "—"

    rows = ""
    for p in plans[:20]:
        d = p.get("decision", "")
        emoji = {"BUY_NOW": "🟢", "BUY_ON_PULLBACK": "🟢", "WATCH": "🟡",
                 "HOLD": "🟠", "SELL": "🔴", "AVOID": "⛔"}.get(d, "")
        code = p.get("code") or "—"
        name = p.get("name") or code  # 名称缺失时回退到代码，避免显示空
        pos = f"{p.get('position_percent')}%" if p.get("position_percent") is not None else "—"
        rr = p.get("risk_reward_1")
        rr_str = f"RR={rr:.2f}" if isinstance(rr, (int, float)) and rr else "RR=—"
        rows += (
            f"<tr><td><b>{emoji} {d}</b></td>"
            f"<td>{code}<br><span style='color:#6b7280;font-size:12px'>{name}</span></td>"
            f"<td>{_p2(p.get('current_price'))}</td>"
            f"<td>{_p2(p.get('entry_low'))}~{_p2(p.get('entry_high'))}</td>"
            f"<td>{_p2(p.get('stop_loss'))}</td>"
            f"<td>{_p2(p.get('target_1'))}/{_p2(p.get('target_2'))}</td>"
            f"<td>{rr_str}</td>"
            f"<td>{pos}</td></tr>"
        )
    return (
        "<table><thead><tr><th>决策</th><th>代码·名称</th><th>现价</th>"
        "<th>入场区间</th><th>止损</th><th>目标1/2</th><th>风险收益</th>"
        "<th>仓位</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
