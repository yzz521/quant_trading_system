"""Model factory — thin wrapper around scikit-learn so model selection is
configurable without touching strategy code."""
from __future__ import annotations

from typing import Any


def build_model(kind: str = "random_forest", **kwargs: Any):
    """Return an unfitted sklearn estimator.

    ``kind`` ∈ {``random_forest``, ``logistic``, ``gradient_boosting``}.
    Raises a helpful error if scikit-learn is missing.
    """
    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
    except ImportError as e:
        raise ImportError(
            "scikit-learn is required for ML features. Run: pip install scikit-learn"
        ) from e

    kind = kind.lower()
    if kind == "random_forest":
        return RandomForestClassifier(
            n_estimators=kwargs.get("n_estimators", 100),
            max_depth=kwargs.get("max_depth", 5),
            random_state=kwargs.get("random_state", 42),
            class_weight=kwargs.get("class_weight", "balanced"),
        )
    if kind == "logistic":
        return LogisticRegression(
            max_iter=kwargs.get("max_iter", 1000),
            class_weight=kwargs.get("class_weight", "balanced"),
        )
    if kind == "gradient_boosting":
        return GradientBoostingClassifier(
            n_estimators=kwargs.get("n_estimators", 100),
            max_depth=kwargs.get("max_depth", 3),
            random_state=kwargs.get("random_state", 42),
        )
    raise ValueError(f"Unknown model kind: {kind}")
