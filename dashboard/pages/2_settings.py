# ruff: noqa: E402
"""配置页 —— 邮件、监测市场、扫描/调度参数。写入 config/notify.yaml。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from quant_trading_system.dashboard.auth import require_login
from quant_trading_system.dashboard.capital import planned_capital
from quant_trading_system.dashboard.paths import notify_config
from quant_trading_system.dashboard.ui_theme import apply_theme, page_header
from quant_trading_system.stock_analysis.app_config import (
    ALL_MARKETS,
    MARKET_LABELS_UI,
    SMTP_PRESETS,
    apply_smtp_preset,
    load_app_config,
    parse_code_list,
    parse_email_list,
    save_app_config,
    smtp_preset_name,
)
from quant_trading_system.stock_analysis.hithink import (
    clear_api_key,
    masked_key,
    save_api_key,
    test_connection,
)
from quant_trading_system.stock_analysis.hithink import (
    is_enabled as hithink_enabled,
)
from quant_trading_system.utils.app_meta import APP_VERSION, GITHUB_RELEASES_PAGE
from quant_trading_system.utils.updater import apply_and_restart, check_latest, is_frozen

apply_theme()
require_login()
page_header("配置", "邮件推送 · 监测市场 · 扫描与调度参数", "Settings")

CFG_PATH = notify_config()
cfg = load_app_config(CFG_PATH)
st.caption(f"配置文件：`{CFG_PATH}`（保存后「今日机会」刷新即可；定时邮件下一轮生效，不必重启应用）")

st.subheader("应用更新")
st.caption(
    f"当前版本 **v{APP_VERSION}** · [GitHub Releases]({GITHUB_RELEASES_PAGE})"
)
if st.button("检查更新", key="chk_upd"):
    try:
        st.session_state["upd_info"] = check_latest()
    except Exception as e:  # noqa: BLE001
        st.session_state.pop("upd_info", None)
        st.error(str(e))
info = st.session_state.get("upd_info")
if info is not None:
    if not info.newer:
        st.success(f"已是最新（{info.latest or ('v' + info.current)}）")
    else:
        st.info(f"发现新版本 **{info.latest}**（当前 v{info.current}）")
        if info.notes:
            with st.expander("更新说明"):
                st.markdown(info.notes)
        if is_frozen() and info.asset_url:
            if st.button("下载并更新（将重启应用）", type="primary", key="do_upd"):
                prog = st.progress(0.0, text="准备下载…")

                def _cb(pct: float, msg: str) -> None:
                    prog.progress(min(max(pct, 0.0), 1.0), text=msg)

                try:
                    apply_and_restart(info, progress=_cb)
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
        else:
            st.markdown(
                f"请到 [GitHub Releases]({info.html_url or GITHUB_RELEASES_PAGE}) "
                f"下载 `{info.asset_name}`。"
            )
            if not is_frozen():
                st.caption("当前是开发模式，应用内更新仅对打包后的 exe / app 有效。")

st.divider()

email_cfg = ((cfg.get("notify") or {}).get("email") or {})
opp_cfg = cfg.get("opportunity") or {}
sched_cfg = cfg.get("schedule") or {}
ai_cfg = cfg.get("ai") or {}
notify_all = cfg.get("notify") or {}
pools = cfg.get("stock_pools") or {}

current_markets = [
    m for m in (cfg.get("enabled_markets") or ["CN"]) if str(m).upper() in ALL_MARKETS
]
if not current_markets:
    current_markets = ["CN"]

# --------------------------------------------------------------------------- #
st.subheader("监测市场")
st.caption("决定「今日机会」扫描哪些市场，以及定时邮件推送哪些市场。持仓页仍可录入任意市场的股票。")
markets = st.multiselect(
    "启用市场",
    options=list(ALL_MARKETS),
    default=current_markets,
    format_func=lambda m: MARKET_LABELS_UI.get(m, m),
    key="cfg_markets",
    help="至少选一个。只选 A 股时扫描最快。",
)

# --------------------------------------------------------------------------- #
st.subheader("A 股数据源")
st.caption(
    "配置后，A 股行情 / 估值 / 财务改走同花顺官方源（更稳定、支持服务端前复权）；"
    "不配置则沿用原数据源。**港股与美股不受影响。**"
)
if hithink_enabled():
    st.success(f"当前状态：同花顺官方源已启用（{masked_key()}）")
else:
    st.info("当前状态：未配置，A 股沿用原数据源（akshare / 新浪 / 腾讯）")

hit_key = st.text_input(
    "同花顺 API Key",
    value="",
    type="password",
    placeholder="留空表示不修改",
    key="hithink_key",
    help="在 fuyao.aicubes.cn 申请。仅保存在本机 config/hithink.env，不上传、也不会打进安装包。",
)
_hc1, _hc2, _hc3 = st.columns(3)
with _hc1:
    if st.button("保存并启用", key="hithink_save"):
        if hit_key.strip():
            if save_api_key(hit_key.strip()):
                st.success("已保存，A 股改用同花顺官方源")
                st.rerun()
            else:
                st.error("保存失败，请检查 config 目录写权限")
        else:
            st.warning("请先粘贴 Key")
with _hc2:
    if st.button("测试连接", key="hithink_test"):
        _ok, _msg = test_connection()
        (st.success if _ok else st.error)(_msg)
with _hc3:
    if hithink_enabled() and st.button("清除并回退", key="hithink_clear"):
        clear_api_key()
        st.success("已清除，A 股回到原数据源")
        st.rerun()

# --------------------------------------------------------------------------- #
st.subheader("邮件推送")
email_on = st.toggle(
    "发送邮件",
    value=bool(email_cfg.get("enabled")),
    help="关闭后调度器仍会分析，但不会发信（只写日志）。",
)
preset_names = list(SMTP_PRESETS.keys())
preset_now = smtp_preset_name(str(email_cfg.get("smtp_host") or ""))
preset = st.selectbox("邮箱类型", preset_names, index=preset_names.index(preset_now))

c1, c2 = st.columns(2)
username = c1.text_input(
    "发件邮箱",
    value=str(email_cfg.get("username") or ""),
    placeholder="you@example.com",
)
to_default = email_cfg.get("to") or []
if isinstance(to_default, str):
    to_default = [to_default]
to_raw = c2.text_input(
    "收件邮箱（逗号分隔，可多个）",
    value=", ".join(str(x) for x in to_default),
    placeholder="you@example.com",
)
password = st.text_input(
    "SMTP 授权码",
    value="",
    type="password",
    help="不是登录密码。QQ/163 需在邮箱设置里生成授权码。留空则保留已保存的授权码。",
)
if email_cfg.get("password"):
    st.caption("已保存授权码（不会显示明文）。要更换请重新填写。")

sender_name = st.text_input("发件人显示名", value=str(email_cfg.get("sender_name") or "GP助手"))

if preset == "自定义":
    s1, s2, s3 = st.columns([2, 1, 1])
    smtp_host = s1.text_input("SMTP 服务器", value=str(email_cfg.get("smtp_host") or "smtp.qq.com"))
    smtp_port = int(s2.number_input("端口", value=int(email_cfg.get("smtp_port") or 465), min_value=1, max_value=65535))
    use_ssl = s3.checkbox("SSL（465）；取消则用 587 STARTTLS", value=bool(email_cfg.get("use_ssl", True)))
else:
    smtp_host = str(email_cfg.get("smtp_host") or "")
    smtp_port = int(email_cfg.get("smtp_port") or 465)
    use_ssl = bool(email_cfg.get("use_ssl", True))
    spec = SMTP_PRESETS[preset]
    if spec:
        st.caption(f"将使用 `{spec[0]}` 端口 {spec[1]}（{'SSL' if spec[2] else 'STARTTLS'}）")

# --------------------------------------------------------------------------- #
st.subheader("今日机会默认参数")
_hold_cap = planned_capital()
if _hold_cap > 0:
    st.caption(f"建议仓位按持仓「总资金」**{_hold_cap:,.0f} 元** 计算。修改请到 holdings 页。")
else:
    st.caption("尚未设置预计投入。进入「今日机会」或 holdings 页填写后才会扫描。")
o1, o3 = st.columns(2)
opp_enabled = o1.toggle("每日邮件包含今日机会", value=bool(opp_cfg.get("enabled")))
max_stocks = int(o3.number_input("每市场候选数", value=int(opp_cfg.get("max_stocks") or 30), min_value=5, max_value=80, step=5))
o4, o5 = st.columns(2)
workers = int(o4.number_input("扫描并发", value=int(opp_cfg.get("workers") or 5), min_value=1, max_value=8))
min_score = float(o5.number_input("机会分下限（0=不过滤）", value=float(opp_cfg.get("min_opportunity_score") or 0), min_value=0.0, max_value=100.0, step=1.0))
index_symbol = st.text_input("市场状态参考指数", value=str(opp_cfg.get("index_symbol") or "sh000001"), help="上证 sh000001；沪深300 sh000300")

# --------------------------------------------------------------------------- #
st.caption("持仓量化每个交易日自动计算一次并写入邮件，与「今日机会」开关无关；历史回测不会定时跑。")
st.subheader("调度频率")
sc1, sc2, sc3 = st.columns(3)
cn_interval = int(sc1.number_input("A股间隔（分钟）", value=int(sched_cfg.get("cn_interval_min") or 60), min_value=5, max_value=240, step=5))
ushk_interval = int(sc2.number_input("美股/港股间隔（分钟）", value=int(sched_cfg.get("ushk_interval_min") or 10), min_value=5, max_value=240, step=5))
us_winter = sc3.checkbox("美股冬令时（21:30 开盘）", value=bool(sched_cfg.get("us_winter", True)))

b1, b2 = st.columns(2)
save_clicked = b1.button("保存配置", type="primary", use_container_width=True)
test_clicked = b2.button("发送测试邮件", use_container_width=True)


def _email_payload(password_value: str) -> dict:
    payload = {
        "enabled": bool(email_on),
        "username": username.strip(),
        "to": parse_email_list(to_raw),
        "sender_name": sender_name.strip() or "GP助手",
        "smtp_host": smtp_host.strip(),
        "smtp_port": int(smtp_port),
        "use_ssl": bool(use_ssl),
    }
    apply_smtp_preset(preset, payload)
    if password_value.strip():
        payload["password"] = password_value.strip()
    elif not email_cfg.get("password"):
        payload["password"] = ""
    return payload


if save_clicked:
    errors: list[str] = []
    if not markets:
        errors.append("请至少选择一个监测市场。")
    email_payload = _email_payload(password)
    if email_payload["enabled"]:
        if not email_payload["username"] or "@" not in email_payload["username"]:
            errors.append("已开启发信：请填写有效的发件邮箱。")
        if not email_payload["to"] and email_payload.get("username"):
            email_payload["to"] = [email_payload["username"]]
        if not email_payload.get("password") and not email_cfg.get("password"):
            errors.append("已开启发信：请填写 SMTP 授权码（首次必须填）。")
        if not email_payload.get("smtp_host"):
            errors.append("请填写 SMTP 服务器。")
    if errors:
        for msg in errors:
            st.error(msg)
    else:
        save_app_config(
            CFG_PATH,
            {
                "enabled_markets": markets,
                "notify": {"email": email_payload},
                "opportunity": {
                    "enabled": bool(opp_enabled),
                    "account_equity": float(planned_capital()),
                    "max_stocks": int(max_stocks),
                    "workers": int(workers),
                    "min_opportunity_score": float(min_score),
                    "index_symbol": index_symbol.strip() or "sh000001",
                },
                "schedule": {
                    "cn_interval_min": cn_interval,
                    "ushk_interval_min": ushk_interval,
                    "us_winter": bool(us_winter),
                },
            },
        )
        st.success("已保存。请到「今日机会」刷新页面使监测市场生效。")
        st.rerun()

if test_clicked:
    if not email_on and not email_cfg.get("enabled"):
        st.warning("请先打开「发送邮件」并保存。")
    else:
        from quant_trading_system.stock_analysis.notifier import Notifier

        try:
            n = Notifier(CFG_PATH)
            if "email" not in n.channels:
                st.warning("邮件渠道未启用。请打开「发送邮件」并保存后再试。")
            else:
                n.send(
                    "GP助手 · 配置测试",
                    "如果你收到这封信，说明 SMTP 配置可用。",
                    html="<p>如果你收到这封信，说明 SMTP 配置可用。</p>",
                )
                st.success("测试邮件已发出，请查收（含垃圾箱）。")
        except Exception as e:  # noqa: BLE001
            st.error(f"发送失败：{e}")

# --------------------------------------------------------------------------- #
class _NoNotifier:
    """状态查询不推送。哨兵对象避免每次页面刷新都重建 Notifier（会刷日志）。"""

    def send(self, *a, **k):  # pragma: no cover - 永不调用
        return {}


with st.expander("实时盯盘（快轨）"):
    st.caption(
        "独立的秒级盯盘通道：只盯持仓/自选，命中规则才推。"
        "上面的「调度频率」负责每 30~60 分钟的全市场选股，这里负责「现在要不要动手」，两者互不干扰。"
    )

    # ---- 运行状态：第一眼就要能看出"它到底有没有在跑" ----
    # 快轨无事发生时是静默的，只看开关状态会把"已启用但线程根本没起来"误判成正常
    st.markdown("**运行状态**")
    try:
        from quant_trading_system.stock_analysis.realtime import RealtimeWatcher as _RW

        _s = _RW(CFG_PATH, notifier=_NoNotifier()).status()
        if not _s["enabled"]:
            st.warning(
                "未启用。打开下面的开关并保存后，"
                "**需重启 GP助手**（或重启单独运行的盯盘命令）才会拉起盯盘线程。"
            )
        elif not _s["heartbeat"]:
            st.error(
                "从未运行过：配置已启用，但没有任何心跳记录 —— 盯盘线程没有被拉起。\n\n"
                "快轨由 GP助手 主程序（`app/main.py` → `RealtimeThread`）启动；"
                "只单独运行看板不会启动它。也可以单独跑：\n\n"
                "`python examples/run_realtime.py`"
            )
        elif _s["alive"]:
            st.success("运行中 —— 心跳正常，正在实时盯盘。")
        else:
            _age = float(_s["heartbeat_age_sec"] or 0)
            st.error(
                f"已停摆 —— 最近一轮在 {_s['heartbeat']}"
                f"（{_age / 60:.1f} 分钟前，超过 {_s['stale_after_sec'] / 60:.0f} 分钟未更新）。"
                "请检查 GP助手 是否还在运行。"
            )

        _m1, _m2, _m3, _m4 = st.columns(4)
        _m1.metric("最近一轮", (_s["heartbeat"] or "—")[11:19] or "—")
        _m2.metric("今日轮数", _s["rounds"])
        _m3.metric("盯盘清单", f"{_s['watch_count']} 只")
        _m4.metric("最近推送", (_s["last_push_at"] or "—")[11:19] or "—")
        st.caption(
            f"轮询：盘中 {_s['interval']}s / 空闲 {_s['idle_interval']}s · "
            f"{'开市中 ' + '/'.join(_s['open_markets']) if _s['in_session'] else '当前休市'}"
            f"（休市时仍会按空闲间隔记账，心跳照常）· 状态文件 {_s['state_path']}"
        )
        if _s["heartbeat"]:
            st.caption(
                f"最近一轮：清单 {_s['last_targets']} 只 · 取到价 {_s['last_quotes']} 只 · "
                f"命中规则 {_s['last_alerts']} 条"
                + (f" · 上次推送 {_s['last_push_n']} 条" if _s["last_push_n"] else "")
            )
        st.caption(
            "看不到心跳时按顺序查：① 盯盘进程是否在运行（GP助手 主程序，或单独跑的 "
            "`examples/run_realtime.py`）② 下方开关是否已保存 ③ 状态文件路径是否存在。"
        )

    except Exception as e:  # noqa: BLE001
        st.caption(f"状态读取失败：{e}")

    rt_cfg = cfg.get("realtime") or {}
    rt_rules = rt_cfg.get("rules") or {}
    rt_on = st.toggle(
        "启用实时盯盘",
        value=bool(rt_cfg.get("enabled", False)),
        key="rt_on",
        help="关闭时不产生任何行情请求，系统行为与未开启时完全一致。",
    )
    rc1, rc2, rc3 = st.columns(3)
    rt_interval = int(rc1.number_input(
        "盘中轮询（秒）", value=int(rt_cfg.get("interval_sec") or 5), min_value=1, max_value=60, key="rt_iv"))
    rt_idle = int(rc2.number_input(
        "午休/休市轮询（秒）", value=int(rt_cfg.get("idle_interval_sec") or 60),
        min_value=10, max_value=600, step=10, key="rt_idle"))
    rt_cool = int(rc3.number_input(
        "同规则冷却（分钟）", value=int(rt_cfg.get("cooldown_min") or 30),
        min_value=1, max_value=240, step=5, key="rt_cool",
        help="同一只票同一条规则在冷却期内只推一次，是防刷屏最关键的一项。"))

    _mode_labels = {"holdings": "只盯持仓", "watchlist": "只盯自选",
                    "both": "持仓 + 自选", "pool": "回退股票池"}
    _mode_keys = list(_mode_labels)
    _mode_now = str(rt_cfg.get("watch_mode") or "holdings")
    rt_mode = st.selectbox(
        "盯盘范围", _mode_keys,
        index=_mode_keys.index(_mode_now) if _mode_now in _mode_keys else 0,
        format_func=lambda k: _mode_labels[k], key="rt_mode")
    _watch_now = rt_cfg.get("watchlist") or []
    rt_watch = st.text_input(
        "额外自选（逗号分隔代码）",
        value=", ".join(str(x) for x in _watch_now), key="rt_watch",
        help="仅在盯盘范围含「自选」时生效，如 513310, 159558")
    rt_levels = st.toggle(
        "自动取关键位（止损位 / 卖出区间下沿）",
        value=bool(rt_cfg.get("levels_from_cache", False)), key="rt_levels",
        help="从当日持仓量化结果读取，穿越时预警；上面手写的价位会覆盖它。"
             "只认当天结果，昨日价位不参与。")

    st.caption("预警规则与阈值")
    _pct = rt_rules.get("pct_change") or {}
    r1, r2, r3 = st.columns(3)
    pc_on = r1.checkbox("涨跌幅异动", value=bool(_pct.get("enabled", True)), key="rt_pct_on")
    pc_stock = r2.number_input("个股 ±%", value=float(_pct.get("cn_stock") or 4.0),
                               min_value=0.5, max_value=20.0, step=0.5, key="rt_pct_stock")
    pc_etf = r3.number_input("ETF ±%", value=float(_pct.get("cn_etf") or 2.0),
                             min_value=0.5, max_value=20.0, step=0.5, key="rt_pct_etf",
                             help="ETF 波动天然小于个股，阈值要单独放宽口径。")

    _spd = rt_rules.get("speed") or {}
    r4, r5, r6 = st.columns(3)
    sp_on = r4.checkbox("急拉 / 急杀", value=bool(_spd.get("enabled", True)), key="rt_sp_on")
    sp_win = r5.number_input("窗口（分钟）", value=int(_spd.get("window_min") or 3),
                             min_value=1, max_value=30, key="rt_sp_win")
    sp_pct = r6.number_input("幅度 ±%", value=float(_spd.get("pct") or 1.0),
                             min_value=0.1, max_value=10.0, step=0.1, key="rt_sp_pct")

    _sl = rt_rules.get("stop_loss") or {}
    _tp = rt_rules.get("take_profit") or {}
    r7, r8, r9, r10 = st.columns(4)
    sl_on = r7.checkbox("成本止损", value=bool(_sl.get("enabled", True)), key="rt_sl_on")
    sl_pct = r8.number_input("止损 %", value=float(_sl.get("pct") if _sl.get("pct") is not None else -8.0),
                             min_value=-50.0, max_value=-1.0, step=0.5, key="rt_sl_pct")
    tp_on = r9.checkbox("成本止盈", value=bool(_tp.get("enabled", True)), key="rt_tp_on")
    tp_pct = r10.number_input("止盈 %", value=float(_tp.get("pct") or 15.0),
                              min_value=1.0, max_value=200.0, step=1.0, key="rt_tp_pct")

    _ts = rt_rules.get("turnover_surge") or {}
    r11, r12 = st.columns(2)
    ts_on = r11.checkbox("换手率异动", value=bool(_ts.get("enabled", False)), key="rt_ts_on")
    ts_pct = r12.number_input("换手率 %", value=float(_ts.get("pct") or 10.0),
                              min_value=1.0, max_value=50.0, step=1.0, key="rt_ts_pct")

    _rt_payload = {
        "enabled": bool(rt_on),
        "interval_sec": int(rt_interval),
        "idle_interval_sec": int(rt_idle),
        "cooldown_min": int(rt_cool),
        "watch_mode": rt_mode,
        "watchlist": parse_code_list(rt_watch),
        "levels_from_cache": bool(rt_levels),
        "rules": {
            "pct_change": {"enabled": bool(pc_on), "cn_stock": float(pc_stock), "cn_etf": float(pc_etf)},
            "speed": {"enabled": bool(sp_on), "window_min": int(sp_win), "pct": float(sp_pct)},
            "stop_loss": {"enabled": bool(sl_on), "pct": float(sl_pct)},
            "take_profit": {"enabled": bool(tp_on), "pct": float(tp_pct)},
            "turnover_surge": {"enabled": bool(ts_on), "pct": float(ts_pct)},
        },
    }
    rs1, rs2 = st.columns(2)
    rt_saved = rs1.button("保存实时盯盘设置", type="primary", use_container_width=True, key="rt_save")
    rt_probe = rs2.button("试跑一轮（只打印，不推送）", use_container_width=True, key="rt_probe")

    if rt_saved:
        save_app_config(CFG_PATH, {"realtime": _rt_payload})
        st.success("已保存。定时邮件下一轮生效；实时盯盘下次轮询即生效。")
        st.rerun()

    if rt_probe:
        from quant_trading_system.stock_analysis.realtime import RealtimeWatcher

        try:
            # 把屏幕上**当前**的设置覆盖进去试跑，不必先保存就能验证；
            # dry_run 不推送也不写状态，探测不会污染正式盯盘的冷却/前值
            _w = RealtimeWatcher(str(CFG_PATH), dry_run=True, overrides={"realtime": _rt_payload})
            st.caption(_w.describe())
            _hits = _w.tick(force=True)
            if _hits:
                _sev = {1: "立即处理", 2: "提示", 3: "资讯"}
                st.dataframe(
                    [{"级别": _sev.get(a.severity, ""), "标的": f"{a.name}({a.code})",
                      "信号": a.title, "说明": a.detail} for a in _hits],
                    use_container_width=True, hide_index=True,
                )
            else:
                st.info("本轮没有告警 —— 阈值未触发即属正常，不代表规则没生效。")
            st.caption("试跑不推送、不写状态，不会影响后续正式盯盘。")
        except Exception as e:  # noqa: BLE001
            st.error(f"试跑失败：{e}")

# --------------------------------------------------------------------------- #
with st.expander("AI 解读（可选）"):
    ai_on = st.toggle("启用 AI 解读", value=bool(ai_cfg.get("enabled")), key="ai_on")
    ai_key = st.text_input(
        "API Key",
        value="",
        type="password",
        key="ai_key",
        help="留空则保留已保存的 Key。也可用环境变量 QTS_AI_API_KEY。",
    )
    if ai_cfg.get("api_key"):
        st.caption("已保存 API Key。")
    ai_url = st.text_input("接口地址", value=str(ai_cfg.get("base_url") or "https://api.deepseek.com"), key="ai_url")
    ai_model = st.text_input("模型", value=str(ai_cfg.get("model") or "deepseek-chat"), key="ai_model")
    if st.button("保存 AI 设置", key="save_ai"):
        ai_update = {
            "enabled": bool(ai_on),
            "base_url": ai_url.strip(),
            "model": ai_model.strip(),
        }
        if ai_key.strip():
            ai_update["api_key"] = ai_key.strip()
        save_app_config(CFG_PATH, {"ai": ai_update})
        st.success("AI 设置已保存")
        st.rerun()

with st.expander("其他推送（Server酱 / 飞书）"):
    sc = notify_all.get("serverchan") or {}
    fs = notify_all.get("feishu") or {}
    sc_on = st.toggle("Server酱微信推送", value=bool(sc.get("enabled")), key="sc_on")
    sc_key = st.text_input("Server酱 SendKey", value="", type="password", key="sc_key")
    if sc.get("sendkey"):
        st.caption("已保存 SendKey。")
    fs_on = st.toggle("飞书机器人", value=bool(fs.get("enabled")), key="fs_on")
    fs_hook = st.text_input("飞书 Webhook", value=str(fs.get("webhook") or ""), key="fs_hook")
    fs_secret = st.text_input("飞书加签密钥（可空）", value="", type="password", key="fs_secret")
    if st.button("保存其他推送", key="save_im"):
        extra = {
            "notify": {
                "serverchan": {"enabled": bool(sc_on)},
                "feishu": {"enabled": bool(fs_on), "webhook": fs_hook.strip()},
            }
        }
        if sc_key.strip():
            extra["notify"]["serverchan"]["sendkey"] = sc_key.strip()
        if fs_secret.strip():
            extra["notify"]["feishu"]["secret"] = fs_secret.strip()
        save_app_config(CFG_PATH, extra)
        st.success("已保存")
        st.rerun()

with st.expander("全市场初筛失败时的回退股票池"):
    st.caption("逗号分隔代码。仅当全市场快照拉不到时使用。")
    pool_cn = st.text_input("A股", value=", ".join(str(x) for x in (pools.get("CN") or [])), key="pool_cn")
    pool_hk = st.text_input("港股", value=", ".join(str(x) for x in (pools.get("HK") or [])), key="pool_hk")
    pool_us = st.text_input("美股", value=", ".join(str(x) for x in (pools.get("US") or [])), key="pool_us")
    if st.button("保存股票池", key="save_pools"):
        save_app_config(
            CFG_PATH,
            {
                "stock_pools": {
                    "CN": parse_code_list(pool_cn),
                    "HK": parse_code_list(pool_hk),
                    "US": parse_code_list(pool_us),
                }
            },
        )
        st.success("股票池已保存")
        st.rerun()
