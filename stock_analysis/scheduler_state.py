"""Persist last scheduler run status for observability (results/scheduler_state.json)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DEFAULT = Path(__file__).resolve().parents[1] / "results" / "scheduler_state.json"


def state_path(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root) / "results" / "scheduler_state.json"
    return _DEFAULT


def load_state(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or state_path()
    if not p.exists():
        return {"markets": {}, "last_any_at": None, "last_error": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"markets": {}, "last_any_at": None, "last_error": None}


def record_run(
    market: str,
    *,
    ok: bool,
    detail: str = "",
    holdings_n: int = 0,
    actions_n: int = 0,
    channels: Optional[list] = None,
    notify_ok: Optional[bool] = None,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    st = load_state(p)
    now = datetime.now(timezone.utc).isoformat()
    markets = st.setdefault("markets", {})
    markets[market] = {
        "at": now,
        "ok": ok,
        "detail": detail[:500],
        "holdings_n": holdings_n,
        "actions_n": actions_n,
        "channels": channels or [],
        "notify_ok": notify_ok,
    }
    st["last_any_at"] = now
    if not ok:
        st["last_error"] = f"[{market}] {detail}"[:500]
    elif (st.get("last_error") or "").startswith(f"[{market}]"):
        st["last_error"] = None
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def format_status_text(st: Optional[dict] = None) -> str:
    st = st if st is not None else load_state()
    lines = ["=== 调度器最近状态 ==="]
    lines.append(f"上次任意市场运行: {st.get('last_any_at') or '尚无'}")
    if st.get("last_error"):
        lines.append(f"最近错误: {st['last_error']}")
    markets = st.get("markets") or {}
    if not markets:
        lines.append("（尚无成功/失败记录，请先: python examples/run_scheduler.py --test --market CN）")
    for m, info in sorted(markets.items()):
        flag = "OK" if info.get("ok") else "FAIL"
        lines.append(
            f"  [{m}] {flag} @ {info.get('at')} | 持仓{info.get('holdings_n', 0)} "
            f"动作{info.get('actions_n', 0)} | 渠道{info.get('channels') or []} "
            f"推送={info.get('notify_ok')} | {info.get('detail', '')}"
        )
    return "\n".join(lines)
