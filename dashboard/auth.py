"""Simple local account gate for Streamlit — cookie 持久登录 + 可配置过期时间.

config/users.yaml 示例::

    enabled: true
    session_ttl_hours: 168   # 默认 7 天；刷新页面保持登录
    session_secret: "换成随机长字符串"
    users:
      - username: admin
        password_hash: "..."

Cookie 名: qts_auth。签名 token = base64(user|expiry|hmac)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional

import streamlit as st
import yaml

_PKG = Path(__file__).resolve().parents[1]
_DEFAULT_USERS = _PKG / "config" / "users.yaml"
_COOKIE_NAME = "qts_auth"
_QUERY_KEY = "qts_auth"
_DEFAULT_TTL_HOURS = 168  # 7 days


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


def _secret(cfg: dict) -> str:
    # 1) 环境变量优先（公网部署建议设置，避免密钥随仓库公开）
    env = os.environ.get("QTS_SESSION_SECRET") or ""
    if env.strip():
        return env.strip()
    # 2) 本地 gitignored 密钥文件 config/secret.local.yaml
    local = _PKG / "config" / "secret.local.yaml"
    try:
        if local.exists():
            lc = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            s = str(lc.get("session_secret") or "").strip()
            if s:
                return s
    except Exception:  # noqa: BLE001
        pass
    # 3) 配置项
    s = str(cfg.get("session_secret") or "").strip()
    if s:
        return s
    # 4) 派生默认密钥（可被公开推导，仅适合纯本机使用）
    return hash_password("qts-session", "qts-local")


def _ttl_seconds(cfg: dict) -> int:
    try:
        hours = float(cfg.get("session_ttl_hours", _DEFAULT_TTL_HOURS))
    except (TypeError, ValueError):
        hours = _DEFAULT_TTL_HOURS
    hours = max(1.0, min(hours, 24 * 90))  # 1h ~ 90d
    return int(hours * 3600)


def make_token(username: str, cfg: Optional[dict] = None) -> tuple[str, int]:
    """Return (token, max_age_seconds)."""
    cfg = cfg or _load_cfg()
    max_age = _ttl_seconds(cfg)
    exp = int(time.time()) + max_age
    payload = f"{username.strip()}|{exp}"
    sig = hmac.new(_secret(cfg).encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    raw = f"{payload}|{sig}".encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token, max_age


def verify_token(token: str, cfg: Optional[dict] = None) -> Optional[str]:
    """Return username if token valid, else None."""
    if not token or not str(token).strip():
        return None
    cfg = cfg or _load_cfg()
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode()
        user, exp_s, sig = raw.split("|", 2)
        exp = int(exp_s)
        if exp < int(time.time()):
            return None
        payload = f"{user}|{exp}"
        expect = hmac.new(_secret(cfg).encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expect, sig):
            return None
        # 用户仍须存在于配置中
        names = {
            str(u.get("username", "")).strip()
            for u in (cfg.get("users") or [])
            if isinstance(u, dict)
        }
        if user.strip() not in names:
            return None
        return user.strip()
    except Exception:
        return None


def _read_cookie(name: str = _COOKIE_NAME) -> Optional[str]:
    # Streamlit 1.37+
    try:
        cookies = st.context.cookies
        if cookies is not None:
            v = cookies.get(name)
            if v:
                return str(v)
    except Exception:
        pass
    # 部分版本
    try:
        from streamlit.web.server.websocket_headers import _get_websocket_headers  # type: ignore

        headers = _get_websocket_headers() or {}
        cookie_hdr = headers.get("Cookie") or headers.get("cookie") or ""
        for part in cookie_hdr.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _set_cookie(name: str, value: str, max_age: int) -> None:
    """通过注入 JS 写入浏览器 Cookie（SameSite=Lax），供刷新/重开兜底恢复。

    写入时机必须是"稳定渲染阶段"：components iframe 异步渲染，若在
    ``st.rerun()`` 前渲染，rerun 会把它清除、JS 来不及执行导致 cookie 写不
    进去。因此 cookie 的写入/删除都由 ``require_login`` 在稳定渲染分支统一
    处理：登录成功经 rerun 回到"session 已有"分支时补写；登出经 rerun 后在
    开头登出分支删除（见 ``require_login`` 注释）。

    注意不要在此处做页面导航（如 ``location.replace``）：components iframe
    的 sandbox 没有 ``allow-top-navigation``，对父窗口的导航会被浏览器静默
    阻止。URL 参数 token 由 ``_set_query_token`` 负责；Streamlit 1.37.x 在
    widget 触发的 rerun 中该更新消息可能丢失（URL 不带 token），此时刷新由
    本 cookie 兜底恢复。
    """
    # 转义
    safe_val = value.replace("\\", "").replace('"', "").replace(";", "")
    safe_name = name.replace("\\", "").replace('"', "").replace(";", "")
    max_age = int(max_age)
    html = f"""
<script>
(function() {{
  try {{
    document.cookie = "{safe_name}={safe_val}; path=/; max-age={max_age}; SameSite=Lax";
  }} catch (e) {{}}
}})();
</script>
"""
    try:
        import streamlit.components.v1 as components

        components.html(html, height=0, width=0)
    except Exception:
        st.markdown(html, unsafe_allow_html=True)


def _clear_cookie(name: str = _COOKIE_NAME) -> None:
    html = f"""
<script>
(function() {{
  try {{
    document.cookie = "{name}=; path=/; max-age=0; SameSite=Lax";
  }} catch (e) {{}}
}})();
</script>
"""
    try:
        import streamlit.components.v1 as components

        components.html(html, height=0, width=0)
    except Exception:
        st.markdown(html, unsafe_allow_html=True)


def _read_query_token() -> Optional[str]:
    try:
        v = st.query_params.get(_QUERY_KEY)
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _set_query_token(token: str) -> None:
    try:
        st.query_params[_QUERY_KEY] = token
    except Exception:  # noqa: BLE001
        pass


def _clear_query_token() -> None:
    try:
        if _QUERY_KEY in st.query_params:
            del st.query_params[_QUERY_KEY]
    except Exception:  # noqa: BLE001
        pass


def _restore_session(cfg: dict) -> Optional[str]:
    """从 URL 参数 token（主）或 Cookie（兜底）恢复登录。"""
    if st.session_state.get("auth_user"):
        return st.session_state["auth_user"]
    token = _read_query_token() or _read_cookie(_COOKIE_NAME)
    if not token:
        return None
    user = verify_token(token, cfg)
    if user:
        st.session_state["auth_user"] = user
        st.session_state["auth_from_cookie"] = True
        return user
    return None


def logout() -> None:
    st.session_state.pop("auth_user", None)
    st.session_state.pop("auth_from_cookie", None)
    st.session_state.pop("auth_token", None)
    _clear_query_token()
    _clear_cookie(_COOKIE_NAME)


def require_login(page_title: str = "量化交易系统") -> bool:
    """未启用门禁时直接通过；已登录或 cookie 有效则通过。"""
    try:
        from quant_trading_system.dashboard.ui_theme import apply_theme

        apply_theme()
    except Exception:
        pass

    cfg = _load_cfg()
    if not cfg.get("enabled"):
        return True

    # 0) 登出处理：清会话状态并在本 run 稳定渲染阶段删 cookie。
    #    _clear_cookie 的 components iframe 若在 st.rerun() 前渲染会被清除
    #    （JS 来不及执行、cookie 删不掉，刷新后又被 cookie 恢复登录），
    #    所以登出标记经 st.rerun() 后在这里统一处理，本 run 不再有 rerun。
    skip_cookie_restore = False
    if st.session_state.pop("_logout_requested", None):
        st.session_state.pop("auth_user", None)
        st.session_state.pop("auth_token", None)
        st.session_state.pop("auth_from_cookie", None)
        _clear_query_token()
        _clear_cookie(_COOKIE_NAME)
        skip_cookie_restore = True

    # 1) session 已有
    if st.session_state.get("auth_user"):
        # 登录成功经 st.rerun() 后回到这里；在此稳定渲染阶段补写 cookie，
        # 避免在 rerun 前写（components iframe 会被 rerun 清除，JS 来不及
        # 执行导致 cookie 写不进去）。cookie 恢复的会话无需重写（token 为空）。
        _auth_token = st.session_state.get("auth_token")
        if _auth_token:
            _set_cookie(_COOKIE_NAME, _auth_token, _ttl_seconds(cfg))
        _sidebar_user(cfg)
        return True

    # 2) cookie 恢复
    if not skip_cookie_restore:
        user = _restore_session(cfg)
        if user:
            _sidebar_user(cfg)
            return True

    # 3) 登录表单
    left, mid, right = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            """
<div class="qts-login-shell">
  <div class="logo">QTS</div>
  <div class="gate">ACCESS GATE</div>
  <p class="sub">登录后会话将保持，刷新页面不会退出（可在 users.yaml 配置过期时间）</p>
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='text-align:center;color:#8b9bb8;font-size:0.9rem;margin:-0.5rem 0 1rem'>{page_title}</div>",
            unsafe_allow_html=True,
        )
        ttl_h = _ttl_seconds(cfg) // 3600
        st.caption(f"会话有效期约 **{ttl_h} 小时**（`session_ttl_hours`）")
        username = st.text_input("用户名", key="login_user", placeholder="admin")
        pwd = st.text_input("密码", type="password", key="login_pwd", placeholder="••••••••")
        ok = st.button("登 录", type="primary", use_container_width=True)
        if ok:
            if verify_user(username, pwd, cfg):
                token, max_age = make_token(username.strip(), cfg)
                st.session_state["auth_user"] = username.strip()
                st.session_state["auth_token"] = token
                # 不在此处写 cookie：st.rerun() 会清除刚渲染的 components
                # iframe，cookie 写不进去；改由 rerun 后的"session 已有"分支
                # 在稳定渲染阶段补写（见 _set_cookie 注释）。
                _set_query_token(token)
                st.success("登录成功，正在进入…")
                st.rerun()
            st.error("用户名或密码错误")
        st.caption("默认账号见 config/users.yaml（不存在时自动放行），生产请改密并设置 session_secret。")
    st.stop()
    return False


def _sidebar_user(cfg: dict) -> None:
    with st.sidebar:
        st.caption(f"已登录：**{st.session_state.get('auth_user', '')}**")
        ttl_h = _ttl_seconds(cfg) // 3600
        st.caption(f"会话约 {ttl_h}h · 刷新保持登录")
        if st.button("退出登录", key="logout_btn"):
            # 不在 rerun 前直接删 cookie（iframe 会被 rerun 清除导致删不掉），
            # 改为设置标记，由 require_login 开头在稳定渲染阶段统一处理。
            st.session_state["_logout_requested"] = True
            st.rerun()
