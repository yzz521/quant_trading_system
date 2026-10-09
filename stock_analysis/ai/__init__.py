"""V2 AI 分析 —— AI 负责解释量化结果，不负责定价。"""
from .ai_analyst import _fallback_explain, explain_plan, explain_plan_with_review
from .guard import (
    DISCLAIMER,
    AIReview,
    build_whitelist,
    check_decision_consistency,
    ensure_disclaimer,
    extract_numbers,
    review_ai_text,
)

__all__ = [
    "explain_plan",
    "explain_plan_with_review",
    "_fallback_explain",
    "AIReview",
    "DISCLAIMER",
    "build_whitelist",
    "check_decision_consistency",
    "ensure_disclaimer",
    "extract_numbers",
    "review_ai_text",
]
