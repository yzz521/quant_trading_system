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
