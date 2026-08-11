"""GP助手 → 本地 Vibe-Trading 二次分析桥接（异步轮询完整回复）。

POST /sessions/{id}/messages 通常只返回 message_id / attempt_id，
真正正文需轮询：
  GET /sessions/{id}/messages
  GET /sessions/{id}/events  (SSE)
  GET /runs 或 GET /runs/{attempt_id}
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..utils import get_logger

log = get_logger("VibeBridge")

DEFAULT_BASE = "http://127.0.0.1:8899"
LLM_SYSTEM_PROMPT = (
    "你是 GP助手 的二次分析引擎（多智能体风格的单一模型执行者）。"
    "按「事实→技术→行业与情绪→风控→结论」五层顺序分析，只输出中文点评正文。"
    "点评必须基于投喂 JSON 的事实，不编造；不要输出过程模板；"
    "不要给自动下单指令，用「可考虑 / 需观察」。"
)
PROMPT_PREFIX = """你是 GP助手 的二次分析引擎。下面 JSON 来自「GP助手」本地持仓系统（事实源）。
请按五步框架分析，并只输出最终中文点评正文：

① 事实层（账本与行情）：以 JSON 为唯一事实源，逐项核对持仓成本/数量/现价/pnl；
② 技术面：结合 candidates 的 matched 条件与评分，简述趋势与动量；
③ 行业与情绪：结合 JSON 中的 industry / industry_category 与新闻 sentiment 字段，
   说明持仓与候选的行业分布、集中度与近期消息情绪；
④ 风控层：单票上限、满仓与可买性、单一行业/金融集中度、深套处理；
⑤ 结论层：给出「可考虑 / 需观察」判断 + 三条纪律提醒 + 免责声明。

硬性要求：
- 只基于 JSON 事实，不编造成本/数量；派生值可心算并标注公式。
- 不要给出自动下单指令；不要输出 Goal/Progress/Remaining Work 等过程模板。
- 正文结构：①一段总括 ②按标的分条（观点+风险）③三条纪律提醒 ④免责声明。
- 明确声明：仅研究参考，非投资建议；未校验实时行情。

JSON 如下：
"""

# Vibe API 的 content 字段实测上限 5000 字符；留余量避免边界抖动
_VIBE_CONTENT_LIMIT = 4700


def _fit_prompt(payload: dict, prefix: str, limit: int = _VIBE_CONTENT_LIMIT) -> str:
    """把投喂 JSON 压缩/裁剪到 Vibe content 长度限制内。

    策略：紧凑 JSON（去缩进）→ 精简 candidates 字段并逐条裁剪数量 → 最后硬截断兜底。
    完整载荷仍会落盘（save_payload），只是发给 Vibe 的正文被收紧。
    """

    def compact(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    body = compact(payload)
    if len(prefix) + len(body) <= limit:
        return prefix + body

    slim = dict(payload)
    slim["candidates"] = []
    base_len = len(prefix) + len(compact(slim))
    kept: list[dict] = []
    slim_fields = ("code", "name", "score", "matched", "close", "change_pct", "matched_days")
    for c in payload.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        item = {k: c.get(k) for k in slim_fields}
        kept.append(item)
        slim["candidates"] = kept
        if len(prefix) + len(compact(slim)) > limit:
            kept.pop()
            break
    slim["candidates"] = kept
    body = compact(slim)
    if len(prefix) + len(body) <= limit:
        return prefix + body

    # 兜底：截断并注明（正常不会走到）
    cut = max(limit - len(prefix) - 60, 120)
    return prefix + body[:cut] + "\n...（内容过长已截断）"


def _results_dir(root: Path) -> Path:
    d = root / "results" / "vibe"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_payload(
    *,
    holdings: list[dict],
    holding_actions: Optional[list] = None,
    capital_snapshot: Optional[dict] = None,
    candidates: Optional[list] = None,
    market: str = "CN",
) -> dict[str, Any]:
    holds = []
    for h in holdings or []:
        holds.append({
            "code": h.get("code"),
            "name": h.get("name"),
            "quantity": h.get("quantity"),
            "cost_price": h.get("cost_price"),
            "current_price": h.get("current_price"),
            "pnl_pct": h.get("pnl_pct"),
            "market": h.get("market", market),
        })
    actions = []
    for a in holding_actions or []:
        if not isinstance(a, dict):
            continue
        actions.append({
            "code": a.get("code"),
            "name": a.get("name"),
            "action": a.get("action") or a.get("label") or a.get("建议"),
            "note": a.get("note") or a.get("reason") or a.get("summary"),
            "pnl_pct": a.get("pnl_pct"),
        })
    cands = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        item = {
            "code": c.get("code"),
            "name": c.get("name"),
            "score": c.get("score"),
            "buy_label": c.get("buy_label"),
            "matched": c.get("matched"),
            "rating": c.get("rating"),
            "close": c.get("close"),
            "change_pct": c.get("change_pct"),
            "matched_days": c.get("matched_days"),
            "signals": c.get("signals"),
        }
        # 漏斗富字段存在才带（扫描命中通常没有）
        for k in ("market_cap", "pe", "turnover", "main_net", "news_risks"):
            if c.get(k) is not None:
                item[k] = c.get(k)
        cands.append(item)
    return {
        "source": "gp_assistant",
        "as_of": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "market": market,
        "capital": capital_snapshot,
        "holdings": holds,
        "holding_actions": actions,
        "candidates": cands,
        "constraints": {
            "no_auto_trade": True,
            "note": "二次分析 only；以 GP助手账本为准；行情失败时勿空等工具",
        },
    }


def enrich_payload(
    payload: dict,
    *,
    industry_map: Optional[dict[str, str]] = None,
    with_chip: bool = True,
) -> dict:
    """给 payload 补充 行业/行业类别 与 持仓筹码 字段；单点失败不中断。"""
    from .data_fetcher import fetch_chip_distribution, fetch_industry_map
    from .industry import category_label, get_industry_category

    try:
        ind_map = industry_map if industry_map is not None else fetch_industry_map()
    except Exception as e:  # noqa: BLE001
        log.debug("行业映射获取失败: %s", e)
        ind_map = {}

    def _annotate(item: dict) -> None:
        code = str(item.get("code") or "").strip().zfill(6)
        ind = ind_map.get(code, "")
        if ind:
            item["industry"] = ind
            item["industry_category"] = category_label(get_industry_category(ind))

    for h in payload.get("holdings") or []:
        if not isinstance(h, dict):
            continue
        _annotate(h)
        if with_chip:
            try:
                chip = fetch_chip_distribution(str(h.get("code") or ""))
                if chip:
                    h["chip"] = chip
            except Exception:  # noqa: BLE001
                pass
    for c in payload.get("candidates") or []:
        if isinstance(c, dict):
            _annotate(c)
    return payload


def save_payload(root: Path, payload: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _results_dir(root) / f"payload_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_latest_scan(root: Path, hits: list, market: str, limit: int = 15) -> Path:
    """把最新一轮扫描命中落盘（供 Vibe 页面/CLI 复用），只保留一份。"""
    path = root / "results" / "latest_scan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "market": market,
        "as_of": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "hits": [dict(h) for h in (hits or [])][: max(int(limit), 1)],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_latest_scan(root: Path) -> dict:
    """读取最新扫描命中；文件缺失/损坏返回空结构。"""
    path = root / "results" / "latest_scan.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("hits"), list):
            return data
    except Exception:  # noqa: BLE001
        pass
    return {"market": "", "as_of": "", "hits": []}


def _http_json(
    method: str,
    url: str,
    body: Optional[dict] = None,
    *,
    timeout: int = 120,
    auth_key: str = "",
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
            if not raw.strip():
                return code, {}
            try:
                return code, json.loads(raw)
            except json.JSONDecodeError:
                return code, {"_raw": raw}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:800]
        try:
            return e.code, json.loads(err)
        except Exception:
            return e.code, {"error": err}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _http_text(url: str, *, timeout: int = 30, auth_key: str = "") -> tuple[int, str]:
    headers = {"Accept": "text/event-stream, application/json, */*"}
    if auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def health(base_url: str = DEFAULT_BASE, auth_key: str = "") -> bool:
    for path in ("/docs", "/runs", "/"):
        code, _ = _http_json("GET", base_url.rstrip("/") + path, timeout=5, auth_key=auth_key)
        if code and code < 500:
            return True
    return False


def _extract_id(obj: Any, *keys: str) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    for k in keys:
        v = obj.get(k)
        if v is not None and str(v).strip():
            return str(v)
    for nest in ("data", "session", "result"):
        sub = obj.get(nest)
        if isinstance(sub, dict):
            found = _extract_id(sub, *keys)
            if found:
                return found
    return None


def _looks_like_ack_only(text: str) -> bool:
    """True if text is only message_id/attempt_id receipt."""
    s = (text or "").strip()
    if not s:
        return True
    if len(s) < 120 and "message_id" in s and "attempt_id" in s:
        return True
    try:
        o = json.loads(s)
        if isinstance(o, dict) and set(o.keys()) <= {
            "message_id", "attempt_id", "id", "status", "ok",
        }:
            return True
    except Exception:
        pass
    return False


def _extract_assistant_texts(obj: Any, acc: Optional[list] = None) -> list[str]:
    acc = acc if acc is not None else []
    if obj is None:
        return acc
    if isinstance(obj, str):
        if len(obj.strip()) > 30 and not _looks_like_ack_only(obj):
            if "GP助手" in obj and "JSON 如下" in obj and len(obj) > 500:
                return acc
            acc.append(obj.strip())
        return acc
    if isinstance(obj, dict):
        role = str(
            obj.get("role") or obj.get("type") or obj.get("sender")
            or obj.get("author") or obj.get("kind") or ""
        ).lower()
        skip_roles = ("user", "human", "system", "tool", "function")
        prefer = role not in skip_roles
        for k in (
            "content", "text", "message", "answer", "reply", "output",
            "summary", "final", "delta", "reasoning", "body", "markdown",
            "assistant_message", "final_answer",
        ):
            v = obj.get(k)
            if isinstance(v, str) and len(v.strip()) > 30 and prefer:
                if not _looks_like_ack_only(v):
                    if not ("JSON 如下" in v and "gp_assistant" in v):
                        acc.append(v.strip())
            elif isinstance(v, list):
                parts = []
                for part in v:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        for pk in ("text", "content", "value"):
                            if isinstance(part.get(pk), str):
                                parts.append(part[pk])
                joined = "\n".join(parts).strip()
                if len(joined) > 30 and prefer and not _looks_like_ack_only(joined):
                    if not ("JSON 如下" in joined and "gp_assistant" in joined):
                        acc.append(joined)
            elif v is not None and not isinstance(v, (str, int, float, bool)):
                _extract_assistant_texts(v, acc)
        for k, v in obj.items():
            if k in (
                "content", "text", "message", "answer", "reply", "output",
                "summary", "final", "delta", "body", "markdown",
            ):
                continue
            if isinstance(v, (dict, list)):
                _extract_assistant_texts(v, acc)
        return acc
    if isinstance(obj, list):
        for x in obj:
            _extract_assistant_texts(x, acc)
        return acc
    return acc


def _best_summary(candidates: list[str]) -> str:
    scored = [c for c in candidates if c and not _looks_like_ack_only(c)]
    if not scored:
        return ""
    scored.sort(key=len, reverse=True)
    return scored[0]


def _parse_sse_data_payloads(raw: str) -> list[Any]:
    payloads = []
    for block in re.split(r"\n\n+", raw or ""):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            continue
        try:
            payloads.append(json.loads(data))
        except json.JSONDecodeError:
            payloads.append(data)
    return payloads


def _poll_reply(
    base: str,
    sid: str,
    attempt_id: Optional[str],
    *,
    auth_key: str,
    max_wait_sec: float,
    poll_sec: float,
) -> tuple[str, dict]:
    collected: list[str] = []
    raw_trace: dict[str, Any] = {"polls": []}
    deadline = time.time() + max_wait_sec

    paths = [
        f"/sessions/{sid}/messages",
        f"/sessions/{sid}",
    ]
    if attempt_id:
        paths.extend([
            f"/runs/{attempt_id}",
            f"/sessions/{sid}/attempts/{attempt_id}",
            f"/attempts/{attempt_id}",
        ])

    while time.time() < deadline:
        for path in paths:
            code, body = _http_json("GET", base + path, timeout=30, auth_key=auth_key)
            raw_trace["polls"].append({"path": path, "code": code})
            if code and code < 400:
                collected.extend(_extract_assistant_texts(body))

        code, runs = _http_json("GET", f"{base}/runs", timeout=20, auth_key=auth_key)
        if code and code < 400:
            collected.extend(_extract_assistant_texts(runs))
            run_list = runs if isinstance(runs, list) else (
                runs.get("runs") or runs.get("items") or runs.get("data")
                if isinstance(runs, dict) else []
            )
            if isinstance(run_list, list):
                for item in run_list[:5]:
                    rid = None
                    if isinstance(item, dict):
                        rid = _extract_id(item, "id", "run_id", "attempt_id")
                    elif isinstance(item, str):
                        rid = item
                    if rid:
                        c2, det = _http_json(
                            "GET", f"{base}/runs/{rid}", timeout=30, auth_key=auth_key,
                        )
                        if c2 and c2 < 400:
                            collected.extend(_extract_assistant_texts(det))

        try:
            code, sse_raw = _http_text(
                f"{base}/sessions/{sid}/events",
                timeout=min(25, max(5, int(poll_sec * 4))),
                auth_key=auth_key,
            )
            if code and code < 400 and sse_raw:
                for p in _parse_sse_data_payloads(sse_raw):
                    collected.extend(_extract_assistant_texts(p))
                raw_trace["last_sse_len"] = len(sse_raw)
        except Exception as e:  # noqa: BLE001
            raw_trace["sse_error"] = str(e)

        best = _best_summary(collected)
        if best and len(best) > 80:
            return best, raw_trace
        time.sleep(poll_sec)

    return _best_summary(collected), raw_trace


def _read_local_session_files(session_id: str) -> str:
    homes = [
        Path.home() / ".vibe-trading",
        Path.home() / ".vibe-trading" / "sessions",
        Path.home() / ".vibe-trading" / "agent" / "sessions",
    ]
    texts: list[str] = []
    for root in homes:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if session_id not in str(p) and session_id not in p.name:
                if "session" not in str(p).lower() and p.suffix not in {".json", ".jsonl", ".md"}:
                    continue
            if p.suffix not in {".json", ".jsonl", ".md", ".txt", ""}:
                continue
            try:
                if p.stat().st_size > 5_000_000:
                    continue
                raw = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if session_id not in raw and session_id not in str(p):
                continue
            if p.suffix == ".jsonl":
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        texts.extend(_extract_assistant_texts(json.loads(line)))
                    except Exception:
                        if len(line) > 80 and not _looks_like_ack_only(line):
                            texts.append(line)
            else:
                try:
                    texts.extend(_extract_assistant_texts(json.loads(raw)))
                except Exception:
                    if len(raw) > 80:
                        texts.extend(_extract_assistant_texts(raw))
    return _best_summary(texts)


def _apply_display_format(out: dict[str, Any]) -> None:
    """Attach clean_summary / display from vibe_format if available."""
    try:
        from .vibe_format import build_display_summary

        disp = build_display_summary(out.get("summary") or "")
        out["partial"] = bool(disp.get("partial"))
        out["display"] = disp
        if disp.get("clean_summary"):
            out["clean_summary"] = disp["clean_summary"]
        if out.get("ok") and out.get("partial"):
            out["error"] = (out.get("error") or "") + "（过程稿已抽取摘要，终稿可能不完整）"
    except Exception as e:  # noqa: BLE001
        out["format_error"] = str(e)


def _save_result(root: Path, out: dict[str, Any]) -> dict[str, Any]:
    _apply_display_format(out)
    result_path = _results_dir(root) / f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    out["result_path"] = str(result_path)
    return out


def submit_secondary_analysis(
    payload: dict,
    *,
    root: Path,
    base_url: str = DEFAULT_BASE,
    auth_key: str = "",
    timeout: int = 180,
    poll_sec: float = 3.0,
    max_wait_sec: float = 300.0,
) -> dict[str, Any]:
    payload_path = save_payload(root, payload)
    prompt = _fit_prompt(payload, PROMPT_PREFIX)
    out: dict[str, Any] = {
        "ok": False,
        "summary": "",
        "session_id": None,
        "attempt_id": None,
        "message_id": None,
        "payload_path": str(payload_path),
        "result_path": None,
        "error": None,
        "raw": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    base = base_url.rstrip("/")
    if not health(base, auth_key):
        out["error"] = f"无法连接 Vibe API ({base})。请先: vibe-trading serve --port 8899"
        out["summary"] = (
            "【离线占位】Vibe 未连通。投喂包已保存。\n"
            f"文件: {payload_path}"
        )
        return _save_result(root, out)

    code, sess = _http_json(
        "POST", f"{base}/sessions",
        {"title": "GP助手二次分析", "name": "gp_assistant"},
        timeout=30, auth_key=auth_key,
    )
    if code >= 400 or code == 0:
        code, sess = _http_json("POST", f"{base}/sessions", {}, timeout=30, auth_key=auth_key)

    sid = _extract_id(sess, "id", "session_id", "sessionId")
    if not sid:
        out["error"] = f"创建 session 失败 HTTP {code}: {sess}"
        out["raw"] = sess
        return _save_result(root, out)
    out["session_id"] = sid

    msg_bodies = [
        {"content": prompt},
        {"message": prompt},
        {"text": prompt},
        {"role": "user", "content": prompt},
    ]
    msg_resp = None
    msg_code = 0
    for body in msg_bodies:
        msg_code, msg_resp = _http_json(
            "POST", f"{base}/sessions/{sid}/messages",
            body, timeout=timeout, auth_key=auth_key,
        )
        if msg_code and msg_code < 400:
            break

    mid = _extract_id(msg_resp, "message_id", "id") if isinstance(msg_resp, dict) else None
    aid = _extract_id(msg_resp, "attempt_id", "run_id") if isinstance(msg_resp, dict) else None
    out["message_id"] = mid
    out["attempt_id"] = aid
    out["raw"] = {"session": sess, "message_ack": msg_resp}

    if not (msg_code and msg_code < 400):
        detail = msg_resp
        if isinstance(msg_resp, dict) and msg_resp.get("error"):
            e = msg_resp["error"]
            try:
                detail = json.loads(e) if isinstance(e, str) else e
            except Exception:  # noqa: BLE001
                detail = e
        out["error"] = f"Vibe 拒绝投喂消息 HTTP {msg_code}：{detail}"
        out["summary"] = "发送失败：Vibe 拒绝了投喂消息，未进入生成流程。"
        return _save_result(root, out)

    immediate = _best_summary(_extract_assistant_texts(msg_resp))
    if immediate and not _looks_like_ack_only(immediate):
        summary = immediate
        poll_trace: dict[str, Any] = {}
    else:
        log.info("Vibe 异步生成中 session=%s attempt=%s，开始轮询…", sid, aid)
        summary, poll_trace = _poll_reply(
            base, sid, aid,
            auth_key=auth_key,
            max_wait_sec=max_wait_sec or 300.0,
            poll_sec=poll_sec,
        )
        out["raw"]["poll"] = poll_trace

    if not summary or _looks_like_ack_only(summary) or len(summary) < 80:
        local = _read_local_session_files(sid)
        if local:
            summary = local
            out["raw"]["local_session_file"] = True

    if summary and not _looks_like_ack_only(summary) and len(summary) > 80:
        out["ok"] = True
        out["summary"] = summary
    else:
        out["ok"] = False
        out["summary"] = summary or (
            f"已提交 session={sid} message={mid} attempt={aid}，"
            "但在等待时间内未解析到助手正文。\n"
            "请打开 http://127.0.0.1:8899 查看该会话；"
            "或稍后再在本页点「刷新历史」。\n"
            f"投喂包: {payload_path}"
        )
        out["error"] = (
            "异步回复未在超时内解析成功（POST 仅返回 id 是正常的）。"
            "请确认 Vibe 日志里该次 attempt 已结束，并检查 GET /sessions/{id}/messages。"
        )

    out = _save_result(root, out)
    log.info(
        "Vibe 二次分析 ok=%s session=%s len=%s",
        out["ok"], sid, len(out.get("summary") or ""),
    )
    return out


def submit_llm_analysis(
    payload: dict,
    *,
    root: Path,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    timeout: int = 180,
    max_tokens: int = 3000,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Vibe 不可用时的 OpenAI 兼容 LLM 兜底（DeepSeek / 通义 / Ollama 等）。

    与 Vibe 共用同一套投喂载荷与正文要求；结果同样落盘 results/vibe/，
    并经 build_display_summary 生成邮件展示。
    """
    from .ai_client import chat_completion

    payload_path = save_payload(root, payload)
    prompt = _fit_prompt(payload, PROMPT_PREFIX)
    out: dict[str, Any] = {
        "ok": False,
        "summary": "",
        "session_id": None,
        "attempt_id": None,
        "message_id": None,
        "payload_path": str(payload_path),
        "result_path": None,
        "error": None,
        "raw": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "llm",
    }
    text = chat_completion(
        [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not text:
        out["error"] = "LLM 兜底未返回正文（检查 ai.api_key / base_url / 网络）"
        out["summary"] = (
            "LLM 兜底调用失败，本次邮件不含二次分析。请检查 config/notify.yaml 的 "
            "ai.api_key / base_url / 网络是否正常（例如 Python 证书库为空时可用 "
            "certifi 兜底），或查看调度器日志中 AIClient 的警告信息后重试。"
        )
        return _save_result(root, out)
    out["ok"] = True
    out["summary"] = text
    out["raw"] = {"provider": base_url, "model": model}
    out = _save_result(root, out)
    log.info("LLM 兜底二次分析 ok=True len=%s", len(text))
    return out


def list_results(root: Path, limit: int = 20) -> list[Path]:
    d = _results_dir(root)
    files = sorted(d.glob("result_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
