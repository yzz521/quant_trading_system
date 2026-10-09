"""快轨状态条：四态判定与文案（纯函数，注入状态字典即可，不需要 Streamlit 运行时）。

守的是什么
----------
"配置已启用"和"线程真的在跑"是两件事，日常使用中最容易互相冒充。这组用例把
四种状态（运行中 / 已停摆 / 未运行 / 未启用）的文案钉住，防止以后有人把它们合并。
"""
from __future__ import annotations

from quant_trading_system.dashboard.realtime_status import (
    badge_html,
    badge_level,
    status_text,
)

_RUNNING = {
    "enabled": True,
    "alive": True,
    "heartbeat": "2026-09-16 10:31:05",
    "heartbeat_age_sec": 3.0,
    "rounds": 128,
    "watch_count": 4,
    "in_session": True,
    "open_markets": ["CN", "HK"],
    "last_push_at": "2026-09-16 10:12:00",
}


def test_badge_level_covers_four_states():
    assert badge_level(_RUNNING) == "ok"
    assert badge_level({**_RUNNING, "alive": False}) == "bad"          # 心跳过期
    assert badge_level({"enabled": True, "alive": False}) == "bad"     # 从未跑过
    assert badge_level({"enabled": False}) == "warn"
    assert badge_level({}) == "off"                                    # 状态读不到


def test_running_text_carries_heartbeat_and_counts():
    t = status_text(_RUNNING)
    assert "运行中" in t
    assert "10:31:05" in t and "3 秒前" in t
    assert "第 128 轮" in t and "清单 4 只" in t
    assert "开市中 CN/HK" in t
    assert "最近推送 10:12:00" in t


def test_stalled_and_never_run_are_distinguishable():
    """最容易误判的一对：配好了但线程没起来 ≠ 跑过之后挂了。"""
    stalled = status_text({**_RUNNING, "alive": False, "heartbeat_age_sec": 740})
    assert "已停摆" in stalled and "12.3 分钟前" in stalled

    never = status_text({"enabled": True, "heartbeat": None, "alive": False})
    assert "未运行" in never
    assert "进程没起来" in never
    assert "已停摆" not in never


def test_closed_market_is_stated_not_hidden():
    """休市也要出声 —— 否则用户以为它坏了。"""
    t = status_text({**_RUNNING, "in_session": False, "open_markets": []})
    assert "当前休市" in t
    assert "运行中" in t


def test_disabled_and_unknown_do_not_raise():
    assert "未启用" in status_text({"enabled": False})
    assert "未知" in status_text({})
    assert status_text({**_RUNNING, "heartbeat_age_sec": None}).startswith("实时盯盘")


def test_badge_html_maps_level_to_css_class():
    assert 'class="qts-rt ok"' in badge_html(_RUNNING)
    assert 'class="qts-rt bad"' in badge_html({"enabled": True, "alive": False})
    assert 'class="qts-rt warn"' in badge_html({"enabled": False})


def test_page_headers_call_the_shared_badge():
    """三个入口页都要挂状态条，别只在配置页里藏一个。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "dashboard"
    for name in ("home.py", "pages/0_opportunity.py", "pages/1_holdings.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "realtime_status import render_badge" in src, name
        assert "render_badge()" in src, name


def test_ui_theme_defines_badge_css():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "dashboard" / "ui_theme.py").read_text(
        encoding="utf-8"
    )
    for cls in (".qts-rt.ok", ".qts-rt.warn", ".qts-rt.bad", ".qts-rt .dot"):
        assert cls in css
