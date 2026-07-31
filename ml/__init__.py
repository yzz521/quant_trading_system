"""Machine-learning research toolbox.

Reusable feature builders, model factories and a walk-forward training
pipeline so you can prototype ML signals without rewriting boilerplate. The
production :class:`~strategy.ml_strategy.MLStrategy` is a thin consumer of
these helpers.
"""
from .features import build_technical_features, label_forward_return
from .models import build_model
from .pipeline import WalkForwardPipeline

__all__ = [
    "build_technical_features",
    "label_forward_return",
    "build_model",
    "WalkForwardPipeline",
]
