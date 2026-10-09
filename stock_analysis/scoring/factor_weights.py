"""按因子验证结果**自动降权** —— 让「回验」真正回到打分公式里。

为什么需要它
------------
``stock_analysis.research.factor_validation`` 已经能算出每个因子的 IC/IR、分组单调性
与滚动前瞻结论，但结论只落在 ``results/factor_validation_report.json`` 里：
**没有人读它，也没有任何东西因为它的结论而改变。**

于是出现最典型的自欺：跑完验证，打印出「confidence 分组不单调、risk_reward_1 排序
含义模糊」，然后打分公式照旧按 45/35/20 加权。验证成了仪式，公式仍是拍脑袋。

本模块把「验证结论」翻译成「组合分权重」，闭环如下：

    results/factor_validation_report.json
        └─ 每个因子的 verdict（可用 / 不单调 / 不稳定 / 无区分力 / 样本不足）
             └─ 映射为维度乘数（1.0 / 0.6 / 0.5 / 0.0 / 1.0）
                  └─ 乘到 COMPOSITE_WEIGHTS 的 stock / opportunity / rr 上
                       └─ 归一化（带单维度上限与维度数下限守卫）
                            └─ 实际参与排序的组合分权重

三条不可退让的守卫
------------------
1. **样本不足不惩罚**。``verdict`` 为「样本不足」意味着*没测出来*，不是*测出来不行*。
   对无证据的因子降权，等于用沉默当反对票，会让「数据少」永久压住「可能有效」的
   因子。此类因子保持原权重，并在 notes 里显式标注「未验证」。
2. **不允许退化成单因子**。若归一化后只剩 1 个维度有权重，等于把全部排序押在一个
   因子上 —— 这比原来的固定权重更危险。此时**回退基线权重**并给出告警说明。
3. **报告过期不套用**。因子有效性会漂移，一份三个月前的报告说明不了当下。
   超过 ``max_age_days`` 一律视为无效，回退基线并说明原因。

设计约束
--------
* 纯离线、无网络；只有 ``load_validated_weights`` / ``active_weights`` 读盘。
* 判定逻辑（``downweight_from_report``）是**纯函数**，给定报告即给定结果，
  便于单测与审计复现。
* 报告缺失 / 损坏 / 过期**绝不抛异常**：打分链路不能因为研究产物缺失而中断，
  只能退化到基线并如实标注。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .composite import COMPOSITE_WEIGHTS

# --------------------------------------------------------------------------- #
# 维度 ↔ 因子 的对应关系
# --------------------------------------------------------------------------- #
# 组合分只有三个维度，因子验证面板里有四个因子：confidence 不属于组合分（它作用于
# 置信度标定那条链路），因此只作为「附注」呈现，不参与权重计算。
DIMENSION_FACTOR: dict[str, str] = {
    "stock": "stock_score",
    "opportunity": "opportunity_score",
    "rr": "risk_reward_1",
}

# verdict 关键词 → 分类（按关键词而非精确匹配，避免 verdict 文案微调就让整条链路失效）
_VERDICT_RULES: tuple[tuple[str, str], ...] = (
    ("样本不足", "insufficient"),
    ("无区分力", "useless"),
    ("方向相反", "inverted"),
    ("不稳定", "unstable"),
    ("不单调", "nonmonotone"),
    ("可用", "usable"),
)

# 分类 → 权重乘数。数值含义：1.0 全额保留、0.6 明显降权、0.5 腰斩、0.0 移出公式。
VERDICT_MULTIPLIER: dict[str, float] = {
    "usable": 1.0,
    "nonmonotone": 0.6,
    "unstable": 0.5,
    "useless": 0.0,
    # 方向相反且显著：本模块只会**按正权重**使用因子，IC 为负说明该因子是
    # 反向指标 —— 继续按正权重用，等于把排序倒过来用，而且"越有效越有害"。
    # 「无信号」只是浪费权重，与它不同档。
    "inverted": 0.0,
    "insufficient": 1.0,   # 守卫 1：没测出来 ≠ 测出来不行
    "unknown": 1.0,        # 报告里没有这个因子 → 同样不给反对票
}

VERDICT_LABEL_ZH: dict[str, str] = {
    "usable": "可用（全额保留）",
    "nonmonotone": "分组不单调（降权至 60%）",
    "unstable": "区分力不稳定（降权至 50%）",
    "useless": "无区分力（移出公式）",
    "inverted": "方向相反（移出公式，否则等于倒着用）",
    "insufficient": "样本不足（不降权，未验证）",
    "unknown": "报告未覆盖（不降权，未验证）",
}

# 守卫 2 的参数
MIN_ACTIVE_DIMENSIONS = 2     # 至少两个维度有权重，否则回退基线
MAX_DIMENSION_WEIGHT = 0.60   # 单维度权重上限（归一化后）

# 守卫 3 的参数
DEFAULT_MAX_AGE_DAYS = 30

_REPORT_RELATIVE_PATH = Path("results") / "factor_validation_report.json"


# --------------------------------------------------------------------------- #
# 纯函数：结论 → 权重
# --------------------------------------------------------------------------- #
def classify_verdict(verdict: Optional[str]) -> str:
    """把 verdict 文案归类。空/None → ``unknown``。"""
    if not verdict:
        return "unknown"
    text = str(verdict)
    for keyword, kind in _VERDICT_RULES:
        if keyword in text:
            return kind
    return "unknown"


def multiplier_for(verdict: Optional[str]) -> float:
    """verdict → 权重乘数。"""
    return VERDICT_MULTIPLIER[classify_verdict(verdict)]


def _verdict_map(report: Optional[dict]) -> dict[str, str]:
    """从报告里抽出 ``{factor: verdict}``。

    兼容两种形态：``summary`` 为 list[dict]（脚本落盘格式），或直接为
    ``{factor: verdict}`` 映射（便于手工写/测试构造）。
    """
    if not isinstance(report, dict):
        return {}
    summary = report.get("summary")
    out: dict[str, str] = {}
    if isinstance(summary, dict):
        for k, v in summary.items():
            out[str(k)] = str(v) if isinstance(v, str) else str((v or {}).get("verdict") or "")
        return out
    if isinstance(summary, list):
        for row in summary:
            if isinstance(row, dict) and row.get("factor"):
                out[str(row["factor"])] = str(row.get("verdict") or "")
    return out


def _renormalize(weights: dict[str, float]) -> dict[str, float]:
    """归一化到和为 1.0。全零时原样返回（由调用方守卫处理）。"""
    total = sum(weights.values())
    if total <= 0:
        return dict(weights)
    return {k: v / total for k, v in weights.items()}


def downweight_from_report(
    report: Optional[dict],
    *,
    base_weights: Optional[dict] = None,
    max_dimension_weight: float = MAX_DIMENSION_WEIGHT,
) -> tuple[dict[str, float], list[str], dict[str, str]]:
    """核心：报告 → 生效权重。

    返回 ``(weights, notes, kinds)``：

    * ``weights`` —— 归一化后的组合分权重（回退时为基线副本）
    * ``notes`` —— 人话说明，可直接进看板 / 报告
    * ``kinds`` —— ``{dimension: verdict 分类}``，便于 UI 着色

    任何异常路径都返回基线权重 + 说明，不抛错。
    """
    base = {**COMPOSITE_WEIGHTS, **(base_weights or {})}
    vmap = _verdict_map(report)
    notes: list[str] = []
    kinds: dict[str, str] = {}

    if not vmap:
        notes.append("未读取到因子验证结论（报告缺失或为空）→ 沿用基线权重。")
        return dict(base), notes, kinds

    scaled: dict[str, float] = {}
    for dim, factor in DIMENSION_FACTOR.items():
        verdict = vmap.get(factor)
        kind = classify_verdict(verdict)
        kinds[dim] = kind
        mult = VERDICT_MULTIPLIER[kind]
        scaled[dim] = base.get(dim, 0.0) * mult
        if kind == "unknown":
            notes.append(
                f"{dim}（因子 {factor}）：报告未覆盖 → 保持基线权重 "
                f"{base.get(dim, 0.0):.2f}（未验证，非降权）。"
            )
        elif kind == "insufficient":
            notes.append(
                f"{dim}（因子 {factor}）：{verdict} → 保持基线权重 "
                f"{base.get(dim, 0.0):.2f}（没测出来不等于测出来不行）。"
            )
        else:
            notes.append(
                f"{dim}（因子 {factor}）：{verdict} → 乘数 {mult:.1f}，"
                f"{base.get(dim, 0.0):.2f} → {scaled[dim]:.3f}（归一化前）。"
            )

    active = [d for d, v in scaled.items() if v > 0]
    if len(active) < MIN_ACTIVE_DIMENSIONS:
        notes.append(
            f"⚠️ 守卫触发：降权后仅剩 {len(active)} 个维度有权重"
            f"（需 ≥{MIN_ACTIVE_DIMENSIONS}）—— 单因子排序比固定权重更危险，"
            f"已回退基线权重，请人工复核验证样本。"
        )
        return dict(base), notes, kinds

    out = _renormalize(scaled)

    # 单维度上限：把超过上限的部分按剩余维度当前权重比例让出去（水填充）。
    over = {d: w for d, w in out.items() if w > max_dimension_weight + 1e-12}
    if over:
        for _ in range(len(out) + 1):
            over = {d: w for d, w in out.items() if w > max_dimension_weight + 1e-12}
            if not over:
                break
            room = [d for d in out if d not in over and out[d] > 0]
            if not room:
                break
            excess = sum(out[d] - max_dimension_weight for d in over)
            for d in over:
                out[d] = max_dimension_weight
            pool = sum(out[d] for d in room)
            for d in room:
                out[d] += excess * (out[d] / pool)
        notes.append(
            f"单维度权重上限 {max_dimension_weight:.0%} 生效（避免降权后过度集中）。"
        )

    out = {d: round(w, 4) for d, w in out.items()}
    # 浮点补偿：把残差补给最大项，保证严格和为 1.0
    diff = round(1.0 - sum(out.values()), 4)
    if abs(diff) >= 1e-4 and out:
        top = max(out, key=lambda k: out[k])
        out[top] = round(out[top] + diff, 4)

    changed = [d for d in out if abs(out[d] - base.get(d, 0.0)) > 1e-9]
    if changed:
        notes.append(
            "生效权重：" + " / ".join(f"{d} {out[d]:.2f}" for d in out)
            + "（基线 " + " / ".join(f"{d} {base.get(d, 0.0):.2f}" for d in out) + "）。"
        )
    else:
        notes.append("各维度结论均未触发降权 → 生效权重与基线一致。")

    conf = vmap.get("confidence")
    if conf:
        notes.append(
            f"附注：confidence（不属于组合分，作用于置信度标定链路）结论为「{conf}」。"
        )
    return out, notes, kinds


# --------------------------------------------------------------------------- #
# 读盘
# --------------------------------------------------------------------------- #
def default_report_path() -> Path:
    """默认报告路径：``<项目根>/results/factor_validation_report.json``。

    优先用环境变量 ``QTS_DATA_DIR``（与运行时数据目录口径一致），便于测试与
    打包后指向用户数据目录。
    """
    import os

    env = os.environ.get("QTS_DATA_DIR")
    if env:
        return Path(env) / _REPORT_RELATIVE_PATH
    return Path(__file__).resolve().parents[2] / _REPORT_RELATIVE_PATH


def _report_time(report: dict, path: Path) -> Optional[datetime]:
    """报告时间：优先 ``generated_at``，否则退回文件 mtime。返回 tz-aware UTC。"""
    raw = report.get("generated_at") if isinstance(report, dict) else None
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def load_validated_weights(
    path: Optional[Any] = None,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    base_weights: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> tuple[dict[str, float], dict]:
    """读报告 → 生效权重。

    返回 ``(weights, meta)``。``meta`` 含 ``applied`` / ``reason`` / ``notes`` /
    ``kinds`` / ``report_path`` / ``age_days``，供看板原样展示。

    守卫 3：报告超过 ``max_age_days`` 天 → 不套用，回退基线并说明。
    """
    base = {**COMPOSITE_WEIGHTS, **(base_weights or {})}
    p = Path(path) if path is not None else default_report_path()
    meta: dict[str, Any] = {
        "report_path": str(p),
        "applied": False,
        "reason": "",
        "notes": [],
        "kinds": {},
        "age_days": None,
        "max_age_days": int(max_age_days),
    }

    if not p.exists():
        meta["reason"] = "验证报告不存在 → 沿用基线权重（先跑 examples/validate_factors.py）。"
        meta["notes"] = [meta["reason"]]
        return dict(base), meta

    try:
        with open(p, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        meta["reason"] = f"验证报告无法解析（{exc}）→ 沿用基线权重。"
        meta["notes"] = [meta["reason"]]
        return dict(base), meta

    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ts = _report_time(report if isinstance(report, dict) else {}, p)
    if ts is not None:
        age_days = (ref - ts).total_seconds() / 86400.0
        meta["age_days"] = round(age_days, 2)
        if age_days > max_age_days:
            meta["reason"] = (
                f"验证报告已过期（{age_days:.0f} 天前，上限 {max_age_days} 天）"
                f"→ 不套用，沿用基线权重。因子有效性会漂移，请重新跑验证。"
            )
            meta["notes"] = [meta["reason"]]
            return dict(base), meta

    weights, notes, kinds = downweight_from_report(report, base_weights=base)
    meta["applied"] = weights != base
    meta["notes"] = notes
    meta["kinds"] = kinds
    meta["reason"] = (
        "已按验证结论调整权重。" if meta["applied"] else "验证结论未触发降权，权重与基线一致。"
    )
    meta["source"] = (report or {}).get("source")
    meta["panel"] = (report or {}).get("panel")
    meta["generated_at"] = (report or {}).get("generated_at")
    return weights, meta


# --------------------------------------------------------------------------- #
# 进程内缓存（排序链路每票都要用，不能每票读一次盘）
# --------------------------------------------------------------------------- #
_CACHE: dict[str, Any] = {"key": None, "at": 0.0, "value": None}
_CACHE_TTL = 600.0


def clear_cache() -> None:
    """清空缓存（测试与「重新验证后立即生效」用）。"""
    _CACHE["key"] = None
    _CACHE["at"] = 0.0
    _CACHE["value"] = None


def active_weights(
    path: Optional[Any] = None,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    base_weights: Optional[dict] = None,
    ttl: float = _CACHE_TTL,
    now: Optional[datetime] = None,
    use_cache: bool = True,
) -> tuple[dict[str, float], dict]:
    """带缓存与 mtime 失效的 ``load_validated_weights``。

    缓存键含报告路径、文件 mtime 与 ``max_age_days``：报告一被重写（mtime 变化）
    或口径改变，缓存立即失效 —— 不会出现「重新验证了但权重还是旧的」。
    """
    p = Path(path) if path is not None else default_report_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = (str(p), mtime, int(max_age_days), tuple(sorted((base_weights or {}).items())))
    mono = time.monotonic()
    if (
        use_cache
        and _CACHE["key"] == key
        and _CACHE["value"] is not None
        and (mono - float(_CACHE["at"])) < ttl
    ):
        return _CACHE["value"]
    value = load_validated_weights(
        p, max_age_days=max_age_days, base_weights=base_weights, now=now
    )
    if use_cache:
        _CACHE.update(key=key, at=mono, value=value)
    return value
