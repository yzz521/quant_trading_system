"""研究性工具：因子验证、组合风控等「上线前先验证」的模块。

与 ``stock_analysis`` 其余部分不同，这里的代码**不参与实时决策**，只用于离线研究
与审计：因子 IC/IR、分组单调性、滚动前瞻样本外、组合暴露与风险度量。
"""
from __future__ import annotations

from .factor_validation import (
    DEFAULT_FACTORS,
    average_ranks,
    build_factor_panel,
    ic_series,
    ic_stats,
    quantile_monotonicity,
    quantile_returns,
    spearman_ic,
    stratified_ic,
    summarize,
    validate_factor,
    validate_factors,
    walk_forward_factor,
    walk_forward_summary,
)

__all__ = [
    "DEFAULT_FACTORS",
    "average_ranks",
    "build_factor_panel",
    "ic_series",
    "ic_stats",
    "quantile_monotonicity",
    "quantile_returns",
    "spearman_ic",
    "stratified_ic",
    "summarize",
    "validate_factor",
    "validate_factors",
    "walk_forward_factor",
    "walk_forward_summary",
]
