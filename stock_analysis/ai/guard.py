"""AI 解读守卫：数值白名单校验 + 决策一致性校验 + 免责声明注入。

要解决的问题
------------
``explain_plan`` 把量化计划的 JSON 交给大模型，并在 prompt 里写「请勿改动任何数值」。
**但 prompt 不是约束**——模型仍可能：

* 编造一个引擎没算过的目标价（「目标价 15.8 元」而引擎给的是 13.0），用户照此下单；
* 与决策矛盾（引擎判 WATCH「观察」，解读里写「建议立即买入」）；
* 漏掉免责声明。

这类错误比「文案不好看」严重得多：它把**未经计算的数字**伪装成系统结论。本模块在
AI 文本进入展示层之前做机器校验，不通过就退回规则化解读——宁可少一段漂亮文案，
也不让用户看到一个假价格。

校验三件事
----------
1. **数值白名单**：文本里出现的数字必须能对应到计划里的某个数值（含百分比/取整等
   等价变体）。带价格语境的未授权数字视为「编造价格」，最高优先级拦截。
2. **决策一致性**：文本不得与 ``plan.decision`` 相反（观察/回避不得出现买入指令，
   卖出不得出现买入/持有指令）。
3. **免责声明**：包含买卖建议时必须附带固定免责声明（合规红线）。

只依赖标准库 re 与 numpy，无网络、无 IO，可离线进 CI。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

# --------------------------------------------------------------------------- #
# 固定免责声明（合规红线：包含买卖建议时必须附带）
# --------------------------------------------------------------------------- #
DISCLAIMER = (
    "⚠️ 免责声明：以上内容由量化模型自动生成，仅供研究参考，不构成任何投资建议。"
    "股市有风险，投资需谨慎，请独立判断并自行承担投资风险。"
)

# --------------------------------------------------------------------------- #
# 文本抽数
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"(?<![\dA-Za-z_.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")

# 价格/金额/比例语境：命中即认为这个数字在陈述「事实数值」，未授权就要拦
_PRICE_CUES = (
    "元", "价", "止损", "目标", "入场", "成本", "现价", "买入", "卖出", "加仓",
    "减仓", "仓位", "支撑", "压力", "阻力", "区间", "市值", "亿", "万", "股",
)

# 计数/单位语境：这些位置上的数字通常是「几部分/几成/几天」，不该当编造价格拦
_BENIGN_TAIL = (
    "字", "部分", "条", "成", "个", "只", "年", "月", "日", "倍", "次", "分",
    "秒", "种", "点", "位", "名", "项", "档", "级", "折", "步",
)


def extract_numbers(text: str) -> list[dict]:
    """抽出文本中的数值及其上下文（用于白名单比对）。

    返回 ``[{value, raw, context, tail}, ...]``；``tail`` 是数字紧随其后的两个字符，
    用于识别「元/价」这类价格语境与「个/天」这类计数语境。
    """
    out: list[dict] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(1)
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append({
            "value": v,
            "raw": raw,
            "context": text[max(0, m.start() - 12): m.end() + 6],
            "tail": text[m.end(): m.end() + 2].strip(),
        })
    return out


# --------------------------------------------------------------------------- #
# 白名单
# --------------------------------------------------------------------------- #
def _walk_numbers(x: Any, out: set[float]) -> None:
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float, np.integer, np.floating)):
        v = float(x)
        if np.isfinite(v):
            out.add(v)
        return
    if isinstance(x, dict):
        for v in x.values():
            _walk_numbers(v, out)
        return
    if isinstance(x, (list, tuple, set)):
        for v in x:
            _walk_numbers(v, out)
        return
    if isinstance(x, str):
        # 字符串里嵌的数字（如 invalidate_condition 里的价位、"1:2.5"）也是事实
        for item in extract_numbers(x):
            out.add(item["value"])


def plan_numbers(plan: Any, *, include_meta: bool = False) -> set[float]:
    """收集计划里出现过的全部数值（白名单来源）。

    ``meta`` 默认排除：它含大量引擎内部参数（阈值、权重），把它们放进白名单会让
    「编造价格」几乎不可能被识别。需要时显式 ``include_meta=True``。
    """
    if plan is None:
        return set()
    d = plan.to_dict() if hasattr(plan, "to_dict") else plan
    if not isinstance(d, dict):
        out: set[float] = set()
        _walk_numbers(d, out)
        return out
    payload = dict(d)
    if not include_meta:
        payload.pop("meta", None)
    out = set()
    _walk_numbers(payload, out)
    return out


def _variants(v: float) -> set[float]:
    """一个数值的等价写法：取整、百分比换算、绝对值。"""
    out = {v, abs(v)}
    for digits in range(0, 5):
        out.add(round(v, digits))
    out.add(round(v * 100.0, 6))          # 0.25 → 25（百分号写法）
    if v != 0:
        out.add(round(1.0 / v, 6))        # 1:2.5 这类比值的倒数写法
    return out


def build_whitelist(plan: Any, *, include_meta: bool = False) -> set[float]:
    """把计划数值展开成允许出现在解读里的数值集合。"""
    allowed: set[float] = set()
    for v in plan_numbers(plan, include_meta=include_meta):
        allowed |= _variants(v)
    return allowed


def is_allowed(value: float, whitelist: set[float], *,
               rel_tol: float = 0.001, abs_tol: float = 0.005) -> bool:
    """数值是否落在白名单容差内（允许展示层四舍五入）。

    容差刻意收紧到 0.1%：价格量级下 0.5% 相对容差会让 11.95 与 11.90 互相匹配，
    编造的价格就能蒙混过关。取整/百分号这类**等价写法**由 ``_variants`` 显式覆盖，
    不需要靠放宽容差来实现。
    """
    for w in whitelist:
        if abs(value - w) <= max(abs_tol, rel_tol * abs(w)):
            return True
    return False


# --------------------------------------------------------------------------- #
# 决策一致性
# --------------------------------------------------------------------------- #
_NEG_PREFIX = "不暂勿别莫免"

_BUY_WORDS = (
    "建议买入", "可以买入", "立即买入", "马上买入", "建议建仓", "可以建仓",
    "建议加仓", "可以加仓", "买入信号", "建议入手", "建议抄底", "可以介入",
)
_NO_BUY_WORDS = (
    "不建议买入", "不要买入", "暂不参与", "建议观望", "建议回避",
    "不建议参与", "暂不介入", "不宜买入", "不建议建仓", "不建议加仓",
)
_SELL_WORDS = ("建议卖出", "立即卖出", "止损离场", "建议清仓", "建议减仓")


def _find_unnegated(text: str, words: tuple[str, ...]) -> list[str]:
    """找出未被否定词修饰的关键词（「不建议买入」不算「建议买入」）。

    注意 ``prev == ""`` 必须单独判断：空串是任意字符串的子串，``"" in "不暂"`` 为
    True，直接写 ``prev not in _NEG_PREFIX`` 会把句首的关键词全部误判成被否定。
    """
    hits: list[str] = []
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            prev = text[i - 1] if i > 0 else ""
            if prev == "" or prev not in _NEG_PREFIX:
                hits.append(w)
            start = i + 1
    return hits


def _decision_value(plan: Any) -> str:
    d = getattr(plan, "decision", None)
    if d is None and isinstance(plan, dict):
        d = plan.get("decision")
    if d is None:
        return ""
    return str(getattr(d, "value", d)).upper()


def check_decision_consistency(text: str, plan: Any) -> list[str]:
    """找出与 ``plan.decision`` 矛盾的表述。"""
    decision = _decision_value(plan)
    if not decision:
        return []
    conflicts: list[str] = []
    buy_hits = _find_unnegated(text, _BUY_WORDS)
    no_buy_hits = _find_unnegated(text, _NO_BUY_WORDS)
    sell_hits = _find_unnegated(text, _SELL_WORDS)

    if decision in ("WATCH", "AVOID") and buy_hits:
        conflicts.append(f"决策为 {decision}（不构成买入条件），解读却出现买入指令：{buy_hits}")
    if decision in ("BUY_NOW", "BUY_ON_PULLBACK") and no_buy_hits:
        conflicts.append(f"决策为 {decision}（满足买入条件），解读却劝阻买入：{no_buy_hits}")
    if decision == "SELL" and buy_hits:
        conflicts.append(f"决策为 SELL，解读却出现买入指令：{buy_hits}")
    if decision == "HOLD" and sell_hits and "止损" not in text:
        conflicts.append(f"决策为 HOLD，解读却给出卖出指令：{sell_hits}")
    return conflicts


# --------------------------------------------------------------------------- #
# 汇总校验
# --------------------------------------------------------------------------- #
@dataclass
class AIReview:
    """AI 解读的校验结论。"""

    passed: bool = True
    invented_prices: list = field(default_factory=list)
    unverified_numbers: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "invented_prices": self.invented_prices,
            "unverified_numbers": self.unverified_numbers,
            "conflicts": self.conflicts,
            "notes": self.notes,
        }

    def describe(self) -> str:
        if self.passed:
            return "校验通过"
        parts = []
        if self.invented_prices:
            parts.append(f"疑似编造价格 {self.invented_prices}")
        if self.conflicts:
            parts.append(f"与决策矛盾 {self.conflicts}")
        return "；".join(parts) or "校验未通过"


def _has_price_context(item: dict) -> bool:
    ctx = str(item.get("context") or "")
    tail = str(item.get("tail") or "")
    if any(cue in ctx for cue in _PRICE_CUES):
        return True
    return any(cue in tail for cue in ("元", "价", "%", "％"))


def _is_benign(item: dict) -> bool:
    """计数/单位语境下的整数不当作编造价格（如「三部分」「5 个交易日」）。"""
    v = item["value"]
    if abs(v - round(v)) > 1e-9:
        return False
    if not (0 <= v <= 20):
        return False
    tail = str(item.get("tail") or "")
    return any(t in tail for t in _BENIGN_TAIL)


def review_ai_text(
    text: str, plan: Any, *, include_meta: bool = False
) -> AIReview:
    """校验 AI 解读：数值白名单 + 决策一致性。"""
    rev = AIReview()
    if not text:
        rev.passed = False
        rev.notes.append("解读为空")
        return rev

    whitelist = build_whitelist(plan, include_meta=include_meta)
    for item in extract_numbers(text):
        if is_allowed(item["value"], whitelist):
            continue
        if _has_price_context(item):
            rev.invented_prices.append(item["raw"])
        elif not _is_benign(item):
            rev.unverified_numbers.append(item["raw"])

    rev.conflicts = check_decision_consistency(text, plan)
    # 只有「编造价格」与「决策矛盾」才拦截；无语境的未授权数字仅作提示，
    # 否则一句「400 字以内」都会让解读被丢弃，守卫会因噪声而失去意义。
    rev.passed = not rev.invented_prices and not rev.conflicts
    if rev.unverified_numbers:
        rev.notes.append(f"未在计划中找到出处的数字：{rev.unverified_numbers}")
    return rev


def ensure_disclaimer(text: str) -> str:
    """确保文本带免责声明（已有则不重复追加）。"""
    if not text:
        return DISCLAIMER
    if "免责声明" in text or "不构成任何投资建议" in text:
        return text
    return text.rstrip() + "\n\n" + DISCLAIMER
