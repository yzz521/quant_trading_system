"""Planned capital gate and GitHub in-app updater helpers."""
from __future__ import annotations

from pathlib import Path

from quant_trading_system.dashboard.capital import planned_capital, save_planned_capital
from quant_trading_system.utils.updater import (
    expected_asset_name,
    is_newer,
    parse_release,
    parse_version,
    write_windows_updater,
)


def test_planned_capital_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path))
    assert planned_capital() == 0.0
    save_planned_capital(150_000)
    assert planned_capital() == 150_000
    yaml_text = (tmp_path / "notify.yaml").read_text(encoding="utf-8")
    assert "150000" in yaml_text or "150000.0" in yaml_text


def test_parse_version_and_newer():
    assert parse_version("v0.3.8") == (0, 3, 8)
    assert parse_version("0.3.7") == (0, 3, 7)
    assert is_newer("v0.3.8", "0.3.7")
    assert not is_newer("v0.3.7", "0.3.7")
    assert not is_newer("v0.3.6", "0.3.7")


def test_parse_release_picks_windows_zip(monkeypatch):
    monkeypatch.setattr(
        "quant_trading_system.utils.updater.expected_asset_name",
        lambda: "GP-Assistant-Windows.zip",
    )
    info = parse_release(
        {
            "tag_name": "v0.3.9",
            "body": "fix",
            "html_url": "https://github.com/yzz521/quant_trading_system/releases/tag/v0.3.9",
            "assets": [
                {
                    "name": "GP-Assistant-Windows.zip",
                    "browser_download_url": "https://example.com/w.zip",
                    "size": 12,
                }
            ],
        },
        current="0.3.8",
    )
    assert info.newer
    assert info.asset_name == "GP-Assistant-Windows.zip"
    assert info.asset_url.endswith("w.zip")


def test_parse_release_synthesizes_download_url(monkeypatch):
    monkeypatch.setattr(
        "quant_trading_system.utils.updater.expected_asset_name",
        lambda: "GP-Assistant-macOS-arm64.zip",
    )
    info = parse_release({"tag_name": "v0.3.12", "assets": []}, current="0.3.11")
    assert info.asset_url.endswith("/v0.3.12/GP-Assistant-macOS-arm64.zip")
    assert "releases/tag/v0.3.12" in info.html_url


def test_latest_tag_from_url_and_atom():
    from quant_trading_system.utils.updater import latest_tag_from_url, parse_latest_from_atom

    assert latest_tag_from_url(
        "https://github.com/yzz521/quant_trading_system/releases/tag/v0.3.12"
    ) == "v0.3.12"
    atom = """<?xml version="1.0"?>
<feed><entry>
  <title>v0.3.12</title>
  <link rel="alternate" href="https://github.com/yzz521/quant_trading_system/releases/tag/v0.3.12"/>
</entry></feed>"""
    assert parse_latest_from_atom(atom) == "v0.3.12"


def test_http_error_text_rate_limit():
    from email.message import Message
    from io import BytesIO
    import urllib.error
    from quant_trading_system.utils.updater import _http_error_text

    hdrs = Message()
    hdrs["X-RateLimit-Remaining"] = "0"
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases/latest",
        403,
        "Forbidden",
        hdrs,
        BytesIO(b'{"message":"API rate limit exceeded for 1.2.3.4."}'),
    )
    assert "限流" in _http_error_text(exc)


def test_check_latest_falls_back_when_api_rate_limited(monkeypatch):
    from email.message import Message
    from io import BytesIO
    import urllib.error
    from quant_trading_system.utils import updater

    class _Html:
        def geturl(self):
            return "https://github.com/yzz521/quant_trading_system/releases/tag/v0.3.12"

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=20):
        url = getattr(req, "full_url", str(req))
        if "api.github.com" in url:
            hdrs = Message()
            hdrs["X-RateLimit-Remaining"] = "0"
            raise urllib.error.HTTPError(
                url,
                403,
                "Forbidden",
                hdrs,
                BytesIO(b'{"message":"API rate limit exceeded for 1.2.3.4."}'),
            )
        if url.rstrip("/").endswith("/releases/latest"):
            return _Html()
        raise AssertionError(url)

    monkeypatch.setattr(updater, "_urlopen", fake_urlopen)
    info = updater.check_latest()
    assert info.latest == "v0.3.12"
    assert info.asset_url.endswith(info.asset_name)
    assert "releases/download/v0.3.12/" in info.asset_url


def test_write_windows_updater_preserves_config(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    bat = tmp_path / "u.bat"
    write_windows_updater(src, dst, bat)
    text = bat.read_text(encoding="ascii")
    assert "robocopy" in text.lower()
    assert "/XD config results" in text
    assert "taskkill" in text.lower()


def test_expected_asset_name_is_ascii():
    name = expected_asset_name()
    assert name.startswith("GP-Assistant-")
    assert name.endswith(".zip") or name.endswith(".tar.gz")
