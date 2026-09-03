"""notify.yaml 配置读写：监测市场、邮件合并、调度热加载。"""
from __future__ import annotations

from pathlib import Path

from quant_trading_system.stock_analysis.app_config import (
    apply_smtp_preset,
    enabled_markets,
    load_app_config,
    normalize_markets,
    parse_code_list,
    parse_email_list,
    save_app_config,
    smtp_preset_name,
)
from quant_trading_system.stock_analysis.scheduler import MarketScheduler
from quant_trading_system.utils import load_yaml, save_yaml


def test_normalize_markets_defaults_to_cn():
    assert normalize_markets(None) == ["CN"]
    assert normalize_markets([]) == ["CN"]
    assert normalize_markets(["hk", "CN", "xx", "HK"]) == ["HK", "CN"]


def test_parse_lists():
    assert parse_email_list("a@x.com, b@y.com；c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert parse_code_list("600519, 00700") == ["600519", "00700"]


def test_save_merges_without_wiping_other_channels(tmp_path: Path):
    path = tmp_path / "notify.yaml"
    save_yaml(
        path,
        {
            "enabled_markets": ["CN", "US"],
            "notify": {
                "email": {"enabled": False, "password": "secret", "username": "a@x.com"},
                "feishu": {"enabled": True, "webhook": "https://example"},
            },
            "opportunity": {"max_stocks": 15},
        },
    )
    save_app_config(
        path,
        {
            "enabled_markets": ["CN", "HK"],
            "notify": {"email": {"enabled": True, "username": "b@x.com"}},
            "opportunity": {"account_equity": 200000},
        },
    )
    cfg = load_yaml(path)
    assert cfg["enabled_markets"] == ["CN", "HK"]
    assert cfg["notify"]["email"]["enabled"] is True
    assert cfg["notify"]["email"]["username"] == "b@x.com"
    assert cfg["notify"]["email"]["password"] == "secret"
    assert cfg["notify"]["feishu"]["webhook"] == "https://example"
    assert cfg["opportunity"]["max_stocks"] == 15
    assert cfg["opportunity"]["account_equity"] == 200000


def test_empty_password_keeps_existing(tmp_path: Path):
    path = tmp_path / "notify.yaml"
    save_app_config(path, {"notify": {"email": {"password": "keep-me", "enabled": False}}})
    save_app_config(path, {"notify": {"email": {"enabled": True, "username": "u@x.com"}}})
    cfg = load_app_config(path)
    assert cfg["notify"]["email"]["password"] == "keep-me"
    assert cfg["notify"]["email"]["enabled"] is True


def test_smtp_preset():
    assert smtp_preset_name("smtp.qq.com") == "QQ 邮箱"
    email = {}
    apply_smtp_preset("Gmail", email)
    assert email["smtp_host"] == "smtp.gmail.com"
    assert email["smtp_port"] == 465
    assert email["use_ssl"] is True


def test_scheduler_reload_picks_up_markets(tmp_path: Path):
    path = tmp_path / "notify.yaml"
    save_yaml(
        path,
        {
            "enabled_markets": ["CN"],
            "stock_pools": {"CN": ["600519"], "HK": ["00700"]},
            "notify": {"email": {"enabled": False}},
            "schedule": {"poll_interval_sec": 60},
        },
    )
    sched = MarketScheduler(str(path))
    assert sched.enabled_markets == ["CN"]
    first_notifier = sched.notifier
    sched.reload()
    assert sched.notifier is first_notifier
    save_app_config(path, {"enabled_markets": ["HK", "US"]})
    sched.reload()
    assert sched.enabled_markets == ["HK", "US"]
    save_app_config(path, {"notify": {"email": {"enabled": False, "username": "n@x.com"}}})
    sched.reload()
    assert sched.notifier is not first_notifier


def test_enabled_markets_helper():
    assert enabled_markets({"enabled_markets": ["us"]}) == ["US"]


def test_settings_page_exists():
    page = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "2_settings.py"
    src = page.read_text(encoding="utf-8")
    assert "发送邮件" in src
    assert "启用市场" in src
    assert "save_app_config" in src
    assert "检查更新" in src
    opp = (page.parent / "0_opportunity.py").read_text(encoding="utf-8")
    assert "enabled_markets" in opp
    assert "planned_capital" in opp
    assert "st.stop()" in opp
    assert 'MARKETS = ["CN"]' not in opp
    assert "step=10_000," not in opp
    assert "step=10_000.0" in opp
