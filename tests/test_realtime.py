"""实时盯盘快轨：交易时段门控、各规则触发口径、冷却去重、分级推送。

全部用例注入假行情源与假通知器，**不触网**，也不依赖真实时钟。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from quant_trading_system.stock_analysis.realtime import (
    SEV_ACTION,
    SEV_INFO,
    SEV_NOTICE,
    RealtimeWatcher,
    RuleEngine,
    in_session,
    install_sigterm_stop,
    is_etf,
)
from quant_trading_system.utils import save_yaml

BEIJING = ZoneInfo("Asia/Shanghai")


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class Clock:
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt

    def advance(self, seconds: float) -> None:
        self.dt = self.dt + timedelta(seconds=seconds)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, title, text, html=None, attachments=None):
        self.sent.append((title, text, html or ""))
        return {"feishu": "ok"}


def make_fetcher(rows_by_call):
    """rows_by_call: 单次 list[dict]，或 list[list[dict]] 按调用顺序返回。"""
    calls: list[list[str]] = []

    def _fetch(codes):
        calls.append(list(codes))
        if rows_by_call and isinstance(rows_by_call[0], list):
            idx = min(len(calls) - 1, len(rows_by_call) - 1)
            rows = rows_by_call[idx]
        else:
            rows = rows_by_call
        return pd.DataFrame(rows) if rows else None

    _fetch.calls = calls  # type: ignore[attr-defined]
    return _fetch


def quote(code="600438", name="通威股份", close=10.0, pct_chg=0.0, turnover=None):
    return {"code": code, "name": name, "close": close, "pct_chg": pct_chg, "turnover": turnover}


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """写一份最小 notify.yaml；状态文件落到 tmp 目录，避免污染真实数据目录。"""
    monkeypatch.setenv("QTS_DATA_DIR", str(tmp_path))

    def _write(realtime: dict) -> str:
        p = tmp_path / "notify.yaml"
        save_yaml(str(p), {"realtime": realtime})
        return str(p)

    return _write


def build(cfg_path, realtime: dict, *, rows, notifier=None, clock=None,
          dry_run=False, overrides=None):
    fetch = make_fetcher(rows)
    w = RealtimeWatcher(
        cfg_path(realtime),
        quote_fetcher=fetch,
        notifier=notifier or FakeNotifier(),
        now_fn=clock or Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING)),
        dry_run=dry_run,
        overrides=overrides,
    )
    w.fetch = fetch  # type: ignore[attr-defined]
    return w


WATCH_CN = {"watch_mode": "watchlist",
            "watchlist": [{"code": "600438", "name": "通威股份", "market": "CN"}]}


# --------------------------------------------------------------------------- #
# 交易时段 / 标的类型
# --------------------------------------------------------------------------- #
def test_in_session_cn_segments():
    def at(hhmm):
        h, m = hhmm.split(":")
        return datetime(2026, 9, 16, int(h), int(m), tzinfo=BEIJING)

    assert in_session("CN", at("09:29")) is False   # 集合竞价尚未连续撮合
    assert in_session("CN", at("09:30")) is True
    assert in_session("CN", at("11:29")) is True
    assert in_session("CN", at("12:00")) is False   # 午休
    assert in_session("CN", at("13:00")) is True
    assert in_session("CN", at("14:59")) is True
    assert in_session("CN", at("15:00")) is False


def test_in_session_weekend_closed():
    sat = datetime(2026, 9, 19, 10, 0, tzinfo=BEIJING)  # 周六
    assert in_session("CN", sat) is False


def test_is_etf_covers_held_positions():
    assert is_etf("513310") is True   # 中韩半导体 ETF（沪）
    assert is_etf("159558") is True   # 深市 ETF
    assert is_etf("600438") is False  # 个股
    assert is_etf("AAPL") is False


# --------------------------------------------------------------------------- #
# 各条规则
# --------------------------------------------------------------------------- #
def test_pct_change_threshold_differs_for_stock_and_etf():
    e = RuleEngine({})
    stock = quote(close=10.4, pct_chg=4.2)
    assert [a.rule for a in e.evaluate(stock, {"market": "CN"})] == ["pct_change"]

    calm = quote(close=10.3, pct_chg=3.0)
    assert e.evaluate(calm, {"market": "CN"}) == []      # 个股未到 ±4%

    etf = quote(code="513310", name="中韩半导体ETF", close=1.05, pct_chg=2.5)
    assert [a.rule for a in e.evaluate(etf, {"market": "CN"})] == ["pct_change"]


def test_pct_change_escalates_when_far_beyond_threshold():
    e = RuleEngine({})
    mild = e.evaluate(quote(pct_chg=4.2), {"market": "CN"})[0]
    wild = e.evaluate(quote(pct_chg=7.0), {"market": "CN"})[0]
    assert mild.severity == SEV_NOTICE
    assert wild.severity == SEV_ACTION   # ≥1.5 倍阈值 → 升级为需立即处理


def test_stop_loss_needs_cost_and_respects_threshold():
    e = RuleEngine({})
    hit = e.evaluate(quote(close=9.1, pct_chg=-3.0), {"market": "CN", "cost_price": 10.0})
    assert [a.rule for a in hit] == ["stop_loss"]
    assert hit[0].severity == SEV_ACTION

    assert e.evaluate(quote(close=9.5), {"market": "CN", "cost_price": 10.0}) == []  # -5% 未到 -8%
    assert e.evaluate(quote(close=9.1), {"market": "CN"}) == []                       # 无成本价→跳过


def test_take_profit_triggers_above_threshold():
    e = RuleEngine({})
    hit = e.evaluate(quote(close=11.6, pct_chg=3.0), {"market": "CN", "cost_price": 10.0})
    assert [a.rule for a in hit] == ["take_profit"]
    assert "不一定" in hit[0].detail or "不机械" in hit[0].detail or "到价不等于必卖" in hit[0].detail


def test_trailing_stop_is_off_by_default_and_works_when_enabled():
    e_off = RuleEngine({})
    ctx = {"market": "CN", "cost_price": 10.0, "peak_price": 12.0}
    assert e_off.evaluate(quote(close=10.5), ctx) == []

    e = RuleEngine({"trailing_stop": {"enabled": True, "start_pct": 5.0, "drawdown_pct": 10.0}})
    # 峰值 12（曾 +20%）→ 现价 10.5，自峰值回撤 12.5% ≥ 10% → 触发
    assert [a.rule for a in e.evaluate(quote(close=10.5), ctx)] == ["trailing_stop"]
    # 未启动追踪（峰值仅 +3%）→ 不触发
    assert e.evaluate(quote(close=9.7), {"market": "CN", "cost_price": 10.0, "peak_price": 10.3}) == []


def test_price_cross_requires_previous_price_and_direction():
    cfg = {"price_cross": {"enabled": True, "levels": {"600438": {"above": 11.0, "below": 9.5}}}}
    e = RuleEngine(cfg)
    # 首轮无前值 → 不触发（避免把"已经在关键位外侧"误报成穿越）
    assert e.evaluate(quote(close=11.2), {"market": "CN"}) == []
    assert [a.rule for a in e.evaluate(quote(close=11.2), {"market": "CN", "prev_price": 10.9})] \
        == ["price_cross_up"]
    assert [a.rule for a in e.evaluate(quote(close=9.4), {"market": "CN", "prev_price": 9.6})] \
        == ["price_cross_down"]


def test_speed_uses_history_window():
    e = RuleEngine({"speed": {"enabled": True, "window_min": 3, "pct": 1.0}})
    now = 1_700_000_000.0
    hist = [[now - 200, 10.0], [now - 60, 10.1]]        # 3 分钟前 ≈ 10.0
    hit = e.evaluate(quote(close=10.2), {"market": "CN", "history": hist, "now_ts": now})
    assert [a.rule for a in hit] == ["speed"]
    assert hit[0].severity == SEV_NOTICE                 # 急拉 → 提示级

    # 样本历史不足（没有窗口之前的点）→ 不判定
    assert e.evaluate(quote(close=10.2), {"market": "CN", "history": [[now - 30, 10.0]], "now_ts": now}) == []

    # 急杀 → 立即处理级
    drop = e.evaluate(quote(close=9.8, pct_chg=-2.0), {"market": "CN", "history": hist, "now_ts": now})
    assert drop[0].severity == SEV_ACTION


def test_turnover_surge_off_by_default():
    assert RuleEngine({}).evaluate(quote(close=10.0, turnover=15.0), {"market": "CN"}) == []
    e = RuleEngine({"turnover_surge": {"enabled": True, "pct": 10.0}})
    hits = e.evaluate(quote(close=10.0, turnover=15.0), {"market": "CN"})
    assert hits[0].severity == SEV_INFO


# --------------------------------------------------------------------------- #
# 盯盘主循环
# --------------------------------------------------------------------------- #
def test_disabled_means_zero_requests(cfg_path):
    w = build(cfg_path, {**WATCH_CN, "enabled": False}, rows=[quote()])
    assert w.tick() == []
    assert w.fetch.calls == []            # 关键：默认关闭时一个请求都不发


def test_tick_pushes_matching_rule(cfg_path):
    n = FakeNotifier()
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote(close=10.5, pct_chg=5.0)], notifier=n)
    hits = w.tick()
    assert len(hits) == 1
    assert len(n.sent) == 1
    assert "通威股份" in n.sent[0][0]


def test_cooldown_suppresses_repeat_then_expires(cfg_path):
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    n = FakeNotifier()
    rows = [quote(close=10.5, pct_chg=5.0)]
    w = build(cfg_path, {**WATCH_CN, "enabled": True, "cooldown_min": 30}, rows=rows, notifier=n, clock=clock)

    assert len(w.tick()) == 1
    clock.advance(60)
    assert w.tick() == []                 # 冷却窗口内不重复推
    assert len(n.sent) == 1
    clock.advance(31 * 60)
    assert len(w.tick()) == 1             # 冷却过期后可再推
    assert len(n.sent) == 2


def test_force_ignores_session_gate(cfg_path):
    # 20:00 已收盘，非 force 不跑；force 照跑（用于 --dry-run 自测）
    off = Clock(datetime(2026, 9, 16, 20, 0, tzinfo=BEIJING))
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote(close=10.5, pct_chg=5.0)], clock=off)
    assert w.tick() == []
    assert len(w.tick(force=True)) == 1


def test_force_quotes_failure_keeps_loop_alive(cfg_path):
    def boom(codes):
        raise RuntimeError("接口超时")

    w = RealtimeWatcher(cfg_path({**WATCH_CN, "enabled": True}), quote_fetcher=boom,
                        notifier=FakeNotifier(),
                        now_fn=Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING)))
    assert w.tick() == []                 # 取数失败只降级，不抛异常


def test_digest_buffers_info_level_alerts(cfg_path):
    n = FakeNotifier()
    rt = {**WATCH_CN, "enabled": True, "digest_min": 5,
          "rules": {"turnover_surge": {"enabled": True, "pct": 10.0}}}
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    w = build(cfg_path, rt, rows=[quote(close=10.0, turnover=15.0)], notifier=n, clock=clock)

    assert w.tick() == []                 # 资讯级先进缓冲，不立即推
    assert n.sent == []
    clock.advance(6 * 60)
    w.tick()                              # 到摘要周期 → 合并推送
    assert len(n.sent) == 1
    assert "摘要" in n.sent[0][0]


def test_peak_price_tracked_for_trailing_stop(cfg_path):
    rt = {**WATCH_CN, "enabled": True,
          "watchlist": [{"code": "600438", "name": "通威股份", "market": "CN"}],
          "rules": {"pct_change": {"enabled": False}, "trailing_stop": {"enabled": True}}}
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    # 无持仓成本价 → trailing_stop 无成本不触发；此处验证峰值确实被记入状态文件
    w = build(cfg_path, rt, rows=[quote(close=12.0, pct_chg=0.0)], clock=clock)
    w.tick()
    state = w._load_state("2026-09-16")
    assert state["peak_price"]["600438"] == 12.0
    assert state["prev_price"]["600438"] == 12.0
    assert len(state["history"]["600438"]) == 1


def test_state_resets_on_new_trading_day(cfg_path):
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    rt = {**WATCH_CN, "enabled": True, "rules": {"pct_change": {"enabled": False}}}
    w = build(cfg_path, rt, rows=[quote(close=12.0)], clock=clock)
    w.tick()
    clock.dt = datetime(2026, 9, 17, 10, 0, tzinfo=BEIJING)   # 次日
    st = w._load_state("2026-09-17")
    assert st["peak_price"] == {} and st["history"] == {}


def test_watchlist_merges_holdings_and_extra_symbols(cfg_path, tmp_path):
    from quant_trading_system.stock_analysis.holdings import Holdings

    save_yaml(str(tmp_path / "holdings.yaml"),
              {"holdings": [{"code": "601398", "name": "工商银行", "market": "CN",
                             "cost_price": 7.975, "quantity": 200}]})
    try:
        Holdings(str(tmp_path / "holdings.yaml")).all()
    except Exception as e:  # noqa: BLE001
        # 某些受限环境里 sqlite 建库会 "disk I/O error"（与本模块逻辑无关）
        pytest.skip(f"持仓库在本机测试临时目录不可用，跳过合并用例: {e}")

    w = build(cfg_path, {"enabled": True, "watch_mode": "both",
                         "watchlist": ["513310"]}, rows=[quote()])
    got = {t["code"]: t for t in w.watchlist()}
    assert got["601398"]["cost_price"] == 7.975      # 持仓带成本价 → 可跑止损/止盈
    assert got["513310"]["name"] == "513310"         # 自选票无成本价


# --------------------------------------------------------------------------- #
# 慢轨喂价位（levels_from_cache）
# --------------------------------------------------------------------------- #
def _write_quant_cache(day: str, items: list[dict], market: str = "CN") -> None:
    from quant_trading_system.stock_analysis.holdings_quant import save_market_cache

    save_market_cache(market, day, items)


def test_plan_levels_from_cache_and_auto_enables_price_cross(cfg_path):
    _write_quant_cache("2026-09-16", [{
        "code": "600438", "name": "通威股份",
        "stop_loss": 9.5, "zone_lo": 12.0, "zone_lo_label": "减仓区下沿",
    }])
    rt = {**WATCH_CN, "enabled": True, "levels_from_cache": True,   # 故意不写 price_cross.enabled
          "rules": {"pct_change": {"enabled": False}}}
    w = build(cfg_path, rt, rows=[quote(close=11.0)])

    assert w.engine.cfg["price_cross"]["enabled"] is True   # 开了自动取价位就该自动打开
    assert w.plan_levels()["600438"] == {
        "below": 9.5, "below_label": "止损位",
        "above": 12.0, "above_label": "卖出区间下沿",
    }


def test_plan_levels_falls_back_to_target_when_no_sell_zone(cfg_path):
    _write_quant_cache("2026-09-16", [{"code": "601398", "stop_loss": 7.5, "target_1": 9.8}])
    w = build(cfg_path, {**WATCH_CN, "enabled": True, "levels_from_cache": True},
              rows=[quote(close=8.0)])
    lv = w.plan_levels()["601398"]
    assert lv["above"] == 9.8 and lv["above_label"] == "第一目标价"


def test_plan_levels_ignores_stale_cache(cfg_path):
    # 昨日价位对今天盯盘是噪音甚至误导，只认当天缓存
    _write_quant_cache("2026-09-15", [{"code": "600438", "stop_loss": 9.5, "zone_lo": 12.0}])
    w = build(cfg_path, {**WATCH_CN, "enabled": True, "levels_from_cache": True},
              rows=[quote(close=11.0)])
    assert w.plan_levels() == {}


def test_plan_levels_off_by_default(cfg_path):
    _write_quant_cache("2026-09-16", [{"code": "600438", "stop_loss": 9.5}])
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote(close=11.0)])
    assert w.levels_from_cache is False
    assert w.plan_levels() == {}


def test_price_cross_uses_auto_levels_end_to_end(cfg_path):
    n = FakeNotifier()
    _write_quant_cache("2026-09-16", [{
        "code": "600438", "name": "通威股份", "stop_loss": 9.5, "zone_lo": 12.0,
    }])
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    rt = {**WATCH_CN, "enabled": True, "levels_from_cache": True,
          "rules": {"pct_change": {"enabled": False}, "speed": {"enabled": False}}}
    # 第一轮 11.0 建立前值；第二轮 12.5 上穿卖出区间下沿 12.0
    w = build(cfg_path, rt, rows=[[quote(close=11.0)], [quote(close=12.5)]],
              notifier=n, clock=clock)
    assert w.tick() == []
    hits = w.tick()
    assert [a.rule for a in hits] == ["price_cross_up"]
    assert "卖出区间下沿" in hits[0].title
    assert len(n.sent) == 1


def test_manual_levels_override_auto_levels():
    e = RuleEngine({"price_cross": {"enabled": True, "levels": {"600438": {"below": 10.0}}}})
    ctx = {"market": "CN", "prev_price": 10.5,
           "auto_levels": {"600438": {"below": 9.5, "below_label": "止损位",
                                      "above": 12.0, "above_label": "卖出区间下沿"}}}
    hits = e.evaluate(quote(close=9.8), ctx)
    assert [a.rule for a in hits] == ["price_cross_down"]
    assert "10" in hits[0].detail          # 手输 10.0 覆盖了自动的 9.5
    assert "止损位" in hits[0].title        # 自动侧带来的语义标签仍保留


def test_price_cross_alert_if_outside_opt_in():
    # 默认：只在"发生穿越"时报，首轮无前值不报
    e = RuleEngine({"price_cross": {"enabled": True, "levels": {"600438": {"below": 10.0}}}})
    assert e.evaluate(quote(close=9.8), {"market": "CN"}) == []

    # 开启后：本来就在关键位外侧的补报一次（深套仓位否则永远等不到 cross 事件）
    e2 = RuleEngine({"price_cross": {"enabled": True, "alert_if_outside": True,
                                     "levels": {"600438": {"below": 10.0}}}})
    hits = e2.evaluate(quote(close=9.8), {"market": "CN"})
    assert [a.rule for a in hits] == ["price_cross_down"]
    assert "已在" in hits[0].title
    # 有前值后回到正常穿越判定，不再重复报"已在"
    assert e2.evaluate(quote(close=9.8), {"market": "CN", "prev_price": 9.9}) == []


# --------------------------------------------------------------------------- #
# 配置页「试跑」用到的两个对外接口
# --------------------------------------------------------------------------- #
def test_describe_summarizes_config(cfg_path):
    """看板不猜属性名，只调 describe()。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True, "interval_sec": 7,
                         "idle_interval_sec": 90, "cooldown_min": 15}, rows=[quote()])
    s = w.describe()
    assert "7 秒" in s and "90 秒" in s and "15 分钟" in s and "1 只" in s


def test_overrides_apply_on_top_of_disk_config(cfg_path):
    """试跑要验证"屏幕上当前的设置"，所以内存覆盖优先于磁盘配置，且不必落盘。"""
    disk = {**WATCH_CN, "enabled": False,
            "rules": {"pct_change": {"enabled": True, "cn_stock": 4.0}}}
    assert build(cfg_path, disk, rows=[quote(close=10.2, pct_chg=2.0)]).tick() == []

    w = build(cfg_path, disk, rows=[quote(close=10.2, pct_chg=2.0)],
              overrides={"realtime": {"enabled": True,
                                      "rules": {"pct_change": {"cn_stock": 0.5}}}})
    assert w.enabled is True
    assert w.engine.cfg["pct_change"]["cn_stock"] == 0.5      # 覆盖生效
    assert "us" not in w.engine.cfg["pct_change"]              # 未覆盖的键保持磁盘原样
    assert [a.rule for a in w.tick()] == ["pct_change"]


def test_overrides_do_not_mutate_caller_dict(cfg_path):
    """覆盖字典不能被 reload 反复合并后污染（否则第二次 reload 会叠加上次的值）。"""
    ov = {"realtime": {"rules": {"pct_change": {"cn_stock": 0.5}}}}
    w = build(cfg_path, {"enabled": True}, rows=[quote()], overrides=ov)
    w.reload()
    w.reload()
    assert ov == {"realtime": {"rules": {"pct_change": {"cn_stock": 0.5}}}}
    assert w.engine.cfg["pct_change"]["cn_stock"] == 0.5


def test_dry_run_does_not_write_state(cfg_path):
    """试跑不该留下冷却/前值去污染正式盯盘。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote(close=10.5, pct_chg=5.0)],
              dry_run=True)
    hits = w.tick(force=True)
    assert len(hits) == 1                       # 该报还是报
    assert not w._state_path().exists()         # 但不落盘


# --------------------------------------------------------------------------- #
# 心跳 / 运行状态：回答"它到底有没有在跑"
# --------------------------------------------------------------------------- #
def _state(w):
    return json.loads(w._state_path().read_text(encoding="utf-8"))


def test_closed_market_still_beats_heartbeat(cfg_path):
    """休市时 tick 不返回告警，但**必须**留下心跳 —— 否则"休市"和"进程已死"分不开。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote()],
              clock=Clock(datetime(2026, 9, 16, 12, 0, tzinfo=BEIJING)))  # 午休
    assert w.tick() == []
    st = _state(w)
    assert st["heartbeat"].startswith("2026-09-16 12:00")
    assert st["rounds"] == 1
    assert st["session_open"] is False
    assert st["last_quotes"] == 0               # 休市不发请求
    assert w.fetch.calls == []                  # type: ignore[attr-defined]


def test_heartbeat_counts_rounds_quotes_and_alerts(cfg_path):
    """正常盘中一轮：轮数 +1、取价数、命中条数都要落盘。"""
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    w = build(cfg_path, {**WATCH_CN, "enabled": True},
              rows=[quote(close=10.5, pct_chg=5.0)], clock=clock)
    assert len(w.tick()) == 1
    st = _state(w)
    assert st["rounds"] == 1
    assert st["session_open"] is True
    assert st["last_targets"] == 1
    assert st["last_quotes"] == 1
    assert st["last_alerts"] == 1
    assert st["last_push_at"].startswith("2026-09-16 10:00")
    assert st["last_push_n"] == 1

    clock.advance(31 * 60)                      # 冷却过后再跑一轮
    w.tick()
    assert _state(w)["rounds"] == 2


def test_quote_failure_still_beats_heartbeat(cfg_path):
    """行情取数失败也要留心跳：一轮失败不等于引擎没在跑。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[])
    assert w.tick() == []
    st = _state(w)
    assert st["rounds"] == 1
    assert st["session_open"] is True           # 客观事实：现在确实在盘中
    assert st["last_quotes"] == 0


def test_zero_rounds_survives_state_reload(cfg_path):
    """回归：rounds=0 / session_open=False 属假值，读盘时不能被 `or` 清成默认。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote()],
              clock=Clock(datetime(2026, 9, 16, 12, 0, tzinfo=BEIJING)))
    w.tick()
    raw = _state(w)
    raw["rounds"] = 0                           # 手工置 0，验证能原样读回
    w._state_path().write_text(json.dumps(raw), encoding="utf-8")
    st = w._load_state("2026-09-16")
    assert st["rounds"] == 0
    assert st["session_open"] is False


def test_status_reports_alive_fresh_and_stale(cfg_path):
    """status() 是看板/CLI 的唯一状态出口，alive 判定要跟心跳年龄走。"""
    clock = Clock(datetime(2026, 9, 16, 10, 0, tzinfo=BEIJING))
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote()], clock=clock)
    w.tick()

    s = w.status()
    assert s["enabled"] is True and s["alive"] is True
    assert s["heartbeat_age_sec"] == 0.0
    assert s["rounds"] == 1 and s["watch_count"] == 1
    assert s["in_session"] is True
    assert "CN" in s["open_markets"]            # 10:00 时 A 股与港股同时开市
    assert s["last_targets"] == 1 and s["last_quotes"] == 1
    assert s["state_path"].endswith("realtime_state.json")

    # 空闲间隔 60s → 容忍 max(60×3,120)=180s；超过即判停摆
    clock.advance(200)
    stale = w.status()
    assert stale["alive"] is False
    assert stale["heartbeat_age_sec"] == 200.0
    assert "已停摆" in w.status_line()

    clock.advance(3600)                         # 跨到 11:03，仍属盘中
    assert w.status()["in_session"] is True


def test_status_line_explains_never_run(cfg_path):
    """配好了但线程根本没起来 —— 这是最容易误判的一种，必须说清原因。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote()])
    line = w.status_line()
    assert "从未跑过" in line
    assert "GP助手" in line                     # 指出真正的拉起方
    assert w.status()["heartbeat"] is None
    assert w.status()["alive"] is False


def test_status_line_handles_disabled(cfg_path):
    w = build(cfg_path, {**WATCH_CN, "enabled": False}, rows=[quote()])
    assert "未启用" in w.status_line()
    assert w.status()["enabled"] is False


# --------------------------------------------------------------------------- #
# 常驻待命 / 优雅退出（deploy 脚本默认拉起快轨依赖这两条）
# --------------------------------------------------------------------------- #
def test_disabled_run_forever_idles_instead_of_exiting(cfg_path):
    """未启用时的常驻循环：不请求行情，也不退场。

    这样 `deploy/ctl.py start-all` 拉起的快轨永远是"活着"的，配置页开关一打开
    就自动开始盯（不必重启进程）；而"有进程但没心跳"才是真的出事了。
    """
    w = build(cfg_path, {**WATCH_CN, "enabled": False, "idle_interval_sec": 10},
              rows=[quote()])
    t = threading.Thread(target=w.run_forever, kwargs={"heartbeat_log_sec": 0}, daemon=True)
    t.start()
    time.sleep(0.3)
    assert t.is_alive()                          # 没有立刻退场
    assert w.fetch.calls == []                   # type: ignore[attr-defined]  零行情请求
    assert w.status()["rounds"] == 0             # 待命期间不记心跳轮次

    w.stop()
    t.join(timeout=3)
    assert not t.is_alive()                      # stop() 能打断 sleep 并退出


def test_enabled_run_forever_ticks_and_beats(cfg_path):
    """对照组：启用后循环里真的会跑轮次并留下心跳。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True, "idle_interval_sec": 10},
              rows=[quote(close=10.5, pct_chg=5.0)])
    t = threading.Thread(target=w.run_forever,
                         kwargs={"heartbeat_log_sec": 0, "reload_every": 10**9},
                         daemon=True)
    t.start()
    time.sleep(0.4)
    w.stop()
    t.join(timeout=3)
    assert _state(w)["rounds"] >= 1
    assert w.fetch.calls != []                   # type: ignore[attr-defined]


def test_install_sigterm_stop_is_skipped_off_main_thread(cfg_path):
    """桌面端 RealtimeThread 是后台线程：装信号处理器必须安全跳过，不能抛异常。"""
    w = build(cfg_path, {**WATCH_CN, "enabled": True}, rows=[quote()])
    got: list[bool] = []
    t = threading.Thread(target=lambda: got.append(install_sigterm_stop(w)))
    t.start()
    t.join(timeout=3)
    assert got == [False]
