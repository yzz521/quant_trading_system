"""V2 AI 分析 —— AI 负责解释量化结果，不负责定价。"""
from .ai_analyst import _fallback_explain, explain_plan

__all__ = ["explain_plan", "_fallback_explain"]
