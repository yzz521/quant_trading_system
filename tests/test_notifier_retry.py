"""Notifier 发送重试离线单测：模拟瞬时网络/DNS 抖动后恢复。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.notifier import Notifier


def _write_cfg(tmp_path, max_attempts=3, backoff=0):
    cfg = tmp_path / "notify.yaml"
    cfg.write_text(
        "notify:\n"
        "  retry:\n"
        f"    max_attempts: {max_attempts}\n"
        f"    backoff_sec: {backoff}\n"
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


def test_retry_then_success(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_send_email(self, title, text, html, attachments):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("[Errno 8] nodename nor servname provided")

    monkeypatch.setattr(Notifier, "_send_email", fake_send_email)
    n = Notifier(_write_cfg(tmp_path, backoff=0))
    res = n.send("标题", "正文", "<p>正文</p>")
    assert res["email"] == "ok"
    assert calls["n"] == 2  # 首次失败 → 第 2 次重试成功


def test_retry_exhausted(tmp_path, monkeypatch):
    def fake_send_email(self, *a, **k):
        raise ConnectionError("[Errno 8] nodename nor servname provided")

    monkeypatch.setattr(Notifier, "_send_email", fake_send_email)
    n = Notifier(_write_cfg(tmp_path, max_attempts=3, backoff=0))
    res = n.send("标题", "正文", "<p>正文</p>")
    assert res["email"].startswith("fail:")
    assert "Errno 8" in res["email"]


def test_no_retry_when_max_attempts_one(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_send_email(self, *a, **k):
        calls["n"] += 1
        raise ConnectionError("boom")

    monkeypatch.setattr(Notifier, "_send_email", fake_send_email)
    n = Notifier(_write_cfg(tmp_path, max_attempts=1, backoff=0))
    res = n.send("标题", "正文", "<p>正文</p>")
    assert res["email"].startswith("fail:")
    assert calls["n"] == 1  # max_attempts=1 → 只试一次，不重试
