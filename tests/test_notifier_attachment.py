"""Notifier 邮件附件离线单测：fake SMTP，检查 MIME 结构。"""
from __future__ import annotations

import email
from email import policy

from quant_trading_system.stock_analysis.notifier import Notifier


def _write_cfg(tmp_path):
    cfg = tmp_path / "notify.yaml"
    cfg.write_text(
        "notify:\n"
        "  email:\n"
        "    enabled: true\n"
        "    smtp_host: 'smtp.test.com'\n"
        "    smtp_port: 465\n"
        "    use_ssl: true\n"
        "    username: 'a@test.com'\n"
        "    password: 'x'\n"
        "    to: ['b@test.com']\n",
        encoding="utf-8",
    )
    return str(cfg)


def test_email_attachment(tmp_path, monkeypatch):
    captured = {}

    class FakeServer:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            pass

        def sendmail(self, frm, to, msg):
            captured["msg"] = msg

        def quit(self):
            pass

    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.notifier.smtplib.SMTP_SSL", FakeServer
    )
    n = Notifier(_write_cfg(tmp_path))
    n.send("标题", "正文", "<p>正文</p>", attachments=[("周报.pdf", b"%PDF-1.4 fake")])

    msg = email.message_from_string(captured["msg"], policy=policy.default)
    parts = list(msg.iter_parts())
    assert any(p.get_content_type() == "multipart/alternative" for p in parts)
    pdf = [p for p in parts if p.get_content_type() == "application/pdf"]
    assert len(pdf) == 1
    assert pdf[0].get_filename() == "周报.pdf"
    assert pdf[0].get_content() == b"%PDF-1.4 fake"


def test_email_without_attachment_still_alternative(tmp_path, monkeypatch):
    captured = {}

    class FakeServer:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            pass

        def sendmail(self, frm, to, msg):
            captured["msg"] = msg

        def quit(self):
            pass

    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.notifier.smtplib.SMTP_SSL", FakeServer
    )
    n = Notifier(_write_cfg(tmp_path))
    n.send("标题", "正文", "<p>正文</p>")
    msg = email.message_from_string(captured["msg"], policy=policy.default)
    assert msg.get_content_type() == "multipart/mixed"  # 外层 mixed，正文仍是 alternative
    assert any(p.get_content_type() == "multipart/alternative" for p in msg.iter_parts())
