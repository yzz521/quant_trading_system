# ruff: noqa: E402
"""配置页 —— 邮件、监测市场、扫描/调度参数。写入 config/notify.yaml。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from quant_trading_system.dashboard.auth import require_login
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

apply_theme()
require_login()
page_header("配置", "邮件推送 · 监测市场 · 扫描与调度参数", "Settings")

CFG_PATH = notify_config()
cfg = load_app_config(CFG_PATH)
st.caption(f"配置文件：`{CFG_PATH}`（保存后「今日机会」刷新即可；定时邮件下一轮生效，不必重启应用）")

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
st.caption("看板「今日机会」页的初始值；也可在该页临时改，不覆盖这里。定时邮件里的机会区块用这里的值。")
o1, o2, o3 = st.columns(3)
opp_enabled = o1.toggle("每日邮件包含今日机会", value=bool(opp_cfg.get("enabled")))
account_equity = o2.number_input(
    "账户资金（元）",
    value=float(opp_cfg.get("account_equity") or 100_000),
    step=10_000.0,
    min_value=0.0,
)
max_stocks = int(o3.number_input("每市场候选数", value=int(opp_cfg.get("max_stocks") or 30), min_value=5, max_value=80, step=5))
o4, o5 = st.columns(2)
workers = int(o4.number_input("扫描并发", value=int(opp_cfg.get("workers") or 5), min_value=1, max_value=8))
min_score = float(o5.number_input("机会分下限（0=不过滤）", value=float(opp_cfg.get("min_opportunity_score") or 0), min_value=0.0, max_value=100.0, step=1.0))
index_symbol = st.text_input("市场状态参考指数", value=str(opp_cfg.get("index_symbol") or "sh000001"), help="上证 sh000001；沪深300 sh000300")

# --------------------------------------------------------------------------- #
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
                    "account_equity": float(account_equity),
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
