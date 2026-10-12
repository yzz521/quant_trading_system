"""A 股收盘报告：交易日 15:05 发，周末和上交所休市不发。

邮件复用盘中 ``build_market_message``，标题带「收盘」，持仓价用日 K 收盘
而不是实时快照。收件人只来自 notify.yaml 的 ``notify.email.to``，测试里用
假 SMTP，不发真邮件。
"""
from __future__ import annotations

import email
from datetime import datetime
from email import policy
from pathlib import Path

import pandas as pd
from quant_trading_system.stock_analysis.scheduler import BEIJING, MarketScheduler
from quant_trading_system.utils import load_yaml, save_yaml
from quant_trading_system.utils.calendar import is_a_share_trading_day

# 测试夹具里的两个邮箱，用来证明「yaml 里配了谁，信就发给谁」。
# 生产代码不写死地址。
_FIXTURE_TO = ["closing-test@163.com", "yzz@mail.grokbot.com"]


def _bj(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=BEIJING)


def _write_cfg(tmp_path, *, recipients=None) -> Path:
    path = tmp_path / "notify.yaml"
    save_yaml(path, {
        "enabled_markets": ["CN"],
        "stock_pools": {"CN": []},
        "notify": {
            "retry": {"max_attempts": 1, "backoff_sec": 0},
            "email": {
                "enabled": True,
                "smtp_host": "smtp.163.com",
                "smtp_port": 465,
                "use_ssl": True,
                "username": "sender@163.com",
                "password": "not-a-real-password",
                "sender_name": "GP助手",
                "to": list(recipients if recipients is not None else _FIXTURE_TO),
            },
        },
        "schedule": {"poll_interval_sec": 60, "cn_interval_min": 60},
        "opportunity": {"enabled": False},
    })
    return path


def _sched(tmp_path, monkeypatch) -> MarketScheduler:
    from quant_trading_system.stock_analysis import scheduler_state as st

    monkeypatch.setattr(st, "_DEFAULT", tmp_path / "scheduler_state.json")
    return MarketScheduler(str(_write_cfg(tmp_path)))


def _quiet_side_effects(sched, monkeypatch) -> None:
    """收盘报告会顺带跑量化、净值、纸面和影子单。测试里这些都不许碰网络。"""
    import quant_trading_system.stock_analysis.scheduler as S

    monkeypatch.setattr(S, "analyze_holding_actions", lambda rows: [])
    monkeypatch.setattr(sched, "_holdings_quant_for_market", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_record_equity_daily", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_track_paper", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_shadow_tick", lambda *a, **k: None)
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.returns_panel.returns_for_holdings",
        lambda holdings, *a, **k: None,
    )


class _FakeSMTP:
    sent: list[dict] = []

    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def sendmail(self, frm, to, msg):
        parsed = email.message_from_string(msg, policy=policy.default)
        _FakeSMTP.sent.append({
            "from": frm,
            "to": list(to),
            "subject": parsed["Subject"],
            "text": _body(parsed, "text/plain"),
            "html": _body(parsed, "text/html"),
        })

    def quit(self):
        pass


def _body(msg, content_type: str) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == content_type:
                return part.get_content()
        return ""
    if msg.get_content_type() == content_type:
        return msg.get_content()
    return ""


def _patch_smtp(monkeypatch) -> None:
    _FakeSMTP.sent = []
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.notifier.smtplib.SMTP_SSL",
        _FakeSMTP,
    )


def _patch_prices(monkeypatch, *, close_today: float, close_prev: float, live: float) -> dict:
    import quant_trading_system.stock_analysis.holdings as H

    calls = {"live": 0, "kline": 0}

    def fake_live(codes):
        calls["live"] += 1
        return {str(c).upper(): live for c in codes}

    def fake_kline(info, days=10):  # noqa: ARG001
        calls["kline"] += 1
        idx = pd.to_datetime(["2026-10-11", "2026-10-12"])
        return pd.DataFrame(
            {"close": [close_prev, close_today]},
            index=idx,
        )

    monkeypatch.setattr(H, "fetch_live_prices", fake_live)
    monkeypatch.setattr(H, "fetch_kline", fake_kline)
    return calls


# --------------------------------------------------------------------------- #
# 日历：周末（含调休上班的周六）与上交所休市
# --------------------------------------------------------------------------- #
def test_calendar_weekend_and_sse_holiday():
    assert is_a_share_trading_day("2026-10-12") is True   # 周一，国庆后的交易日
    assert is_a_share_trading_day("2026-10-11") is False  # 周日
    # 2026-10-10 国务院调休上班，上交所仍休市
    assert is_a_share_trading_day("2026-10-10") is False
    assert is_a_share_trading_day("2026-10-01") is False  # 周四，国庆休市


def test_fires_on_trading_day_at_1505(tmp_path, monkeypatch):
    sched = _sched(tmp_path, monkeypatch)
    fired = []
    monkeypatch.setattr(sched, "_run_closing_report", lambda now: fired.append(now))
    monkeypatch.setattr(sched, "_in_session", lambda market, now=None: False)

    now = _bj(2026, 10, 12, 15, 5)
    assert sched.closing_report_due(now) is True
    sched._tick(now, now_ts=0.0, last={"CN": 0.0, "HK": 0.0, "US": 0.0})
    assert len(fired) == 1
    assert fired[0].date().isoformat() == "2026-10-12"

    # 同一天再轮询一次不能发第二封
    sched._tick(now, now_ts=120.0, last={"CN": 0.0, "HK": 0.0, "US": 0.0})
    assert len(fired) == 1


def test_skipped_on_weekend(tmp_path, monkeypatch):
    sched = _sched(tmp_path, monkeypatch)
    fired = []
    monkeypatch.setattr(sched, "_run_closing_report", lambda now: fired.append(now))
    for day in (11, 10):  # 周日，以及调休上班的周六
        now = _bj(2026, 10, day, 15, 5)
        assert sched.closing_report_due(now) is False
        sched._tick(now, now_ts=0.0, last={"CN": 0.0, "HK": 0.0, "US": 0.0})
    assert fired == []


def test_skipped_on_holiday(tmp_path, monkeypatch):
    sched = _sched(tmp_path, monkeypatch)
    fired = []
    monkeypatch.setattr(sched, "_run_closing_report", lambda now: fired.append(now))
    now = _bj(2026, 10, 1, 15, 5)  # 国庆，周四
    assert sched.closing_report_due(now) is False
    sched._tick(now, now_ts=0.0, last={"CN": 0.0, "HK": 0.0, "US": 0.0})
    assert fired == []


def test_not_due_before_1505_or_after_window(tmp_path, monkeypatch):
    sched = _sched(tmp_path, monkeypatch)
    assert sched.closing_report_due(_bj(2026, 10, 12, 15, 4)) is False
    assert sched.closing_report_due(_bj(2026, 10, 12, 9, 30)) is False
    assert sched.closing_report_due(_bj(2026, 10, 12, 15, 30)) is True
    assert sched.closing_report_due(_bj(2026, 10, 12, 16, 0)) is False


def test_closing_report_title_close_price_and_recipients(tmp_path, monkeypatch):
    """标题含收盘、现价是当日收盘价、经 Notifier 发给 yaml 里的每一个收件人。"""
    sched = _sched(tmp_path, monkeypatch)
    _quiet_side_effects(sched, monkeypatch)
    _patch_smtp(monkeypatch)
    calls = _patch_prices(
        monkeypatch, close_today=1800.25, close_prev=1700.0, live=111.11,
    )
    sched.holdings.add(
        "600519", name="贵州茅台", market="CN",
        cost_price=1000.0, quantity=10, buy_date="2026-01-05",
    )

    now = _bj(2026, 10, 12, 15, 5)
    sched._tick(now, now_ts=0.0, last={"CN": 0.0, "HK": 0.0, "US": 0.0})

    assert calls["live"] == 0, "收盘报告不能走实时快照"
    assert calls["kline"] >= 1
    assert len(_FakeSMTP.sent) == 1
    mail = _FakeSMTP.sent[0]

    configured = list(load_yaml(tmp_path / "notify.yaml")["notify"]["email"]["to"])
    assert mail["to"] == configured
    assert len(configured) == 2
    assert any(str(a).lower().endswith("@163.com") for a in configured)
    assert "yzz@mail.grokbot.com" in [str(a).lower() for a in configured]

    assert mail["subject"] == "GP助手 · A股 收盘 2026-10-12"
    assert "收盘" in mail["subject"]
    assert "现价1800.25" in mail["text"]
    assert "111.11" not in mail["text"]
    assert "现价1700" not in mail["text"]
    assert "收盘报告" in mail["html"]
    assert "1800.25" in mail["html"]


def test_closing_message_reuses_hourly_template(tmp_path, monkeypatch):
    """收盘与盘中共用 build_market_message：区块还在，只有标题和页眉不同。"""
    from quant_trading_system.stock_analysis.notifier import build_market_message

    holdings = [{
        "code": "600519", "name": "贵州茅台", "quantity": 10, "cost_price": 1000.0,
        "current_price": 1800.25, "pnl": 8002.5, "pnl_pct": 80.03,
    }]
    summary = {
        "total_cost": 10000.0, "total_value": 18002.5, "total_pnl": 8002.5,
        "total_pnl_pct": 80.03, "count": 1,
    }
    title, text, html = build_market_message(
        "CN", holdings=holdings, holdings_summary=summary,
        closing=True, as_of=_bj(2026, 10, 12, 15, 5),
    )
    assert title == "GP助手 · A股 收盘 2026-10-12"
    assert "收盘" in title
    assert "💼 我的A股持仓" in html
    assert "收盘报告" in html
    assert "现价1800.25" in text

    hourly, _, hourly_html = build_market_message(
        "CN", holdings=holdings, holdings_summary=summary,
    )
    assert hourly.startswith("GP助手 · A股 ")
    assert "收盘" not in hourly
    assert "开盘期间定时推送" in hourly_html
    assert "💼 我的A股持仓" in hourly_html


def test_hourly_pnl_still_uses_live_quote(tmp_path, monkeypatch):
    """默认口径仍是实时价。收盘路径不能把小时报一起改掉。"""
    import quant_trading_system.stock_analysis.holdings as H
    from quant_trading_system.stock_analysis.holdings import Holdings

    h = Holdings(str(tmp_path / "holdings.yaml"))
    h.add("600519", name="贵州茅台", market="CN", cost_price=1000.0, quantity=10)

    monkeypatch.setattr(H, "fetch_live_prices", lambda codes: {"600519": 111.11})

    def _no_kline(*a, **k):
        raise AssertionError("实时价已经取到，小时报不应再拉日 K")

    monkeypatch.setattr(H, "fetch_kline", _no_kline)
    rows, _ = h.compute_pnl("CN")
    assert rows[0]["current_price"] == 111.11

