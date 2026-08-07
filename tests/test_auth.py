"""登录 token / 会话密钥离线单测（不打网络）。"""
from __future__ import annotations

import time

from quant_trading_system.dashboard import auth


def _cfg(**kw) -> dict:
    base = {
        "enabled": True,
        "session_ttl_hours": 168,
        "session_secret": "test-secret",
        "users": [{"username": "admin", "password_hash": auth.hash_password("pw")}],
    }
    base.update(kw)
    return base


def test_token_roundtrip():
    token, max_age = auth.make_token("admin", _cfg())
    assert max_age > 0
    assert auth.verify_token(token, _cfg()) == "admin"


def test_expired_token_rejected(monkeypatch):
    cfg = _cfg(session_ttl_hours=1)
    token, _ = auth.make_token("admin", cfg)
    monkeypatch.setattr(auth.time, "time", lambda: time.time() + 7200)
    assert auth.verify_token(token, cfg) is None


def test_tampered_token_rejected():
    token, _ = auth.make_token("admin", _cfg())
    bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert auth.verify_token(bad, _cfg()) is None


def test_unknown_user_rejected():
    token, _ = auth.make_token(
        "nobody",
        _cfg(users=[{"username": "admin", "password_hash": "x"}]),
    )
    assert auth.verify_token(token, _cfg()) is None


def test_secret_precedence_env(monkeypatch):
    monkeypatch.setenv("QTS_SESSION_SECRET", "env-secret")
    assert auth._secret(_cfg(session_secret="cfg-secret")) == "env-secret"
    monkeypatch.delenv("QTS_SESSION_SECRET")


def test_secret_precedence_local_file(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "secret.local.yaml").write_text(
        'session_secret: "file-secret"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("QTS_SESSION_SECRET", raising=False)
    monkeypatch.setattr(auth, "_PKG", tmp_path)
    assert auth._secret(_cfg(session_secret="cfg-secret")) == "file-secret"


def test_secret_default_derived(monkeypatch, tmp_path):
    monkeypatch.delenv("QTS_SESSION_SECRET", raising=False)
    monkeypatch.setattr(auth, "_PKG", tmp_path)  # 隔离本地 config/secret.local.yaml
    assert auth._secret({"session_secret": ""}) == auth.hash_password("qts-session", "qts-local")
