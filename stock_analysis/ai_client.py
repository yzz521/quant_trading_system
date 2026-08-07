"""OpenAI-compatible chat client (DeepSeek / 通义兼容 / OpenAI / 本地网关).

配置见 config/notify.yaml 的 ai: 段。未启用或缺少 key 时返回 None，不影响主流程。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from ..utils import get_logger

log = get_logger("AIClient")


def chat_completion(
    messages: list[dict[str, str]],
    *,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    timeout: int = 60,
    temperature: float = 0.3,
    max_tokens: int = 1200,
) -> Optional[str]:
    """Return assistant text or None on failure."""
    if not api_key or not str(api_key).strip():
        return None
    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        choices = payload.get("choices") or []
        if not choices:
            log.warning("AI 返回空 choices: %s", payload)
            return None
        content = choices[0].get("message", {}).get("content")
        return (content or "").strip() or None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        log.warning("AI HTTP %s: %s", e.code, err)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("AI 调用失败: %s", e)
        return None


def cfg_from_notify(notify_cfg: dict[str, Any]) -> dict[str, Any]:
    """Extract ai block with defaults."""
    ai = (notify_cfg or {}).get("ai") or {}
    return {
        "enabled": bool(ai.get("enabled", False)),
        "api_key": str(ai.get("api_key") or __import__("os").environ.get("QTS_AI_API_KEY") or "").strip(),
        "base_url": str(ai.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        "model": str(ai.get("model") or "deepseek-chat"),
        "timeout": int(ai.get("timeout") or 60),
        "max_tokens": int(ai.get("max_tokens") or 1200),
        "temperature": float(ai.get("temperature") or 0.3),
    }
