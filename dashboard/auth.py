"""Account gate — futuristic login card."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Optional

import streamlit as st
import yaml

_PKG = Path(__file__).resolve().parents[1]
_DEFAULT_USERS = _PKG / "config" / "users.yaml"


def _load_cfg(path: Optional[Path] = None) -> dict:
    p = path or _DEFAULT_USERS
    if not p.exists():
        return {"enabled": False, "users": []}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {"enabled": False, "users": []}
    except Exception:
        return {"enabled": False, "users": []}


def hash_password(password: str, salt: str = "qts-local") -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def verify_user(username: str, password: str, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or _load_cfg()
    for u in cfg.get("users") or []:
        if not isinstance(u, dict):
            continue
        if str(u.get("username", "")).strip() != username.strip():
            continue
        if u.get("password") is not None and str(u.get("password")) == password:
            return True
        expected = str(u.get("password_hash") or "")
        salt = str(u.get("salt") or "qts-local")
        if expected and hmac.compare_digest(expected, hash_password(password, salt)):
            return True
    return False


def require_login(page_title: str = "量化交易系统") -> bool:
    from quant_trading_system.dashboard.ui_theme import apply_theme
    apply_theme()

    cfg = _load_cfg()
    if not cfg.get("enabled"):
        return True

    if st.session_state.get("auth_user"):
        with st.sidebar:
            st.caption(f"已登录：**{st.session_state['auth_user']}**")
            if st.button("退出登录", key="logout_btn"):
                st.session_state.pop("auth_user", None)
                st.rerun()
        return True

    # Centered futuristic card (no empty placeholder bars)
    left, mid, right = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            """
<div class="qts-login-shell">
  <div class="logo">QTS</div>
  <div class="gate">ACCESS GATE</div>
  <p class="sub">外网访问请先登录 · 本地持仓助手</p>
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='text-align:center;color:#8b9bb8;font-size:0.9rem;margin:-0.5rem 0 1rem'>{page_title}</div>",
            unsafe_allow_html=True,
        )
        user = st.text_input("用户名", key="login_user", placeholder="admin")
        pwd = st.text_input("密码", type="password", key="login_pwd", placeholder="••••••••")
        ok = st.button("登 录", type="primary", use_container_width=True)
        if ok:
            if verify_user(user, pwd, cfg):
                st.session_state["auth_user"] = user.strip()
                st.rerun()
            st.error("用户名或密码错误")
        st.caption("默认示例密码请见 config/users.yaml.example，生产环境务必修改。")
    st.stop()
    return False
