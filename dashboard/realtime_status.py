"""快轨（实时盯盘）状态条 —— 让「它到底有没有在跑」在**主页面**一眼可见。

为什么单独一个模块
------------------
快轨无事发生时是静默的（命中才推送），只看配置开关会把「已启用但线程压根没起来」
误判成正常。过去这个状态只藏在「配置 → 实时盯盘」里，而这恰恰是平时最少去的页面，
于是「在盯」和「死了」在日常使用时完全分不开。

这里不自己解析 ``realtime_state.json``，只调 ``RealtimeWatcher.status()`` ——
状态文件结构会变，引擎的对外方法是唯一稳定契约。
"""
from __future__ import annotations

import streamlit as st

from quant_trading_system.dashboard.paths import notify_config


class _NoNotifier:
    """查状态不推送。哨兵对象避免每次页面刷新都重建 Notifier（会刷日志）。"""

    def send(self, *a, **k):  # pragma: no cover - 永不调用
        return {}


def load_status() -> dict:
    """读一次快轨状态；失败返回 ``{}`` 而不是抛错（状态读不到不该把整页搞崩）。"""
    try:
        from quant_trading_system.stock_analysis.realtime import RealtimeWatcher

        return RealtimeWatcher(notify_config(), notifier=_NoNotifier()).status()
    except Exception:  # noqa: BLE001
        return {}


def _hhmmss(stamp: object) -> str:
    s = str(stamp or "")
    return s[11:19] if len(s) >= 19 else "—"


def _ago(seconds: object) -> str:
    try:
        v = float(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{v:.0f} 秒前" if v < 90 else f"{v / 60:.1f} 分钟前"


def badge_level(status: dict) -> str:
    """四种状态：``ok`` 运行中 / ``bad`` 未运行或已停摆 / ``warn`` 未启用 / ``off`` 读不到。"""
    if not status:
        return "off"
    if not status.get("enabled"):
        return "warn"
    if not status.get("heartbeat"):
        return "bad"
    return "ok" if status.get("alive") else "bad"


def status_text(status: dict) -> str:
    """状态条上那半句话（纯函数，方便单测）。"""
    if not status:
        return "实时盯盘 · 状态未知"
    if not status.get("enabled"):
        return "实时盯盘 · 未启用（配置 → 实时盯盘）"
    if not status.get("heartbeat"):
        return "实时盯盘 · 未运行 —— 配置已启用但无心跳，盯盘进程没起来"
    if not status.get("alive"):
        return (f"实时盯盘 · 已停摆 —— 最近一轮 {_hhmmss(status.get('heartbeat'))}"
                f"（{_ago(status.get('heartbeat_age_sec'))}）")
    market = "开市中 " + "/".join(status.get("open_markets") or []) \
        if status.get("in_session") else "当前休市"
    detail = (f"第 {status.get('rounds', 0)} 轮 · 清单 {status.get('watch_count', 0)} 只 · "
              f"{market}")
    if status.get("last_push_at"):
        detail += f" · 最近推送 {_hhmmss(status['last_push_at'])}"
    return (f"实时盯盘 · 运行中 —— {_hhmmss(status.get('heartbeat'))}"
            f"（{_ago(status.get('heartbeat_age_sec'))}）· {detail}")


def badge_html(status: dict) -> str:
    """状态条 HTML（纯函数；样式在 ``ui_theme._CSS`` 的 ``.qts-rt``）。"""
    return (
        f'<div class="qts-rt {badge_level(status)}">'
        f'<span class="dot"></span>'
        f'<span class="k">快轨</span>'
        f'<span class="sep">|</span>'
        f'<span>{status_text(status)}</span>'
        f"</div>"
    )


def render_badge() -> None:
    """在主页面顶部渲染状态条。"""
    st.markdown(badge_html(load_status()), unsafe_allow_html=True)
