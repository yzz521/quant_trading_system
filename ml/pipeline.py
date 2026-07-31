"""Walk-forward training pipeline.

Trains a model on a rolling window, predicts the next segment, then rolls
forward — the standard way to avoid look-ahead when evaluating an ML signal.
Returns out-of-sample predictions aligned to the original index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import build_technical_features, label_forward_return
from .models import build_model


class WalkForwardPipeline:
    def __init__(self, train_size: int = 500, step: int = 100,
                 horizon: int = 1, model_kind: str = "random_forest",
                 **model_kwargs) -> None:
        self.train_size = train_size
        self.step = step
        self.horizon = horizon
        self.model_kind = model_kind
        self.model_kwargs = model_kwargs

    def run(self, close: pd.Series) -> pd.Series:
        """Return out-of-sample predicted probabilities (class 1)."""
        X = build_technical_features(close)
        y = label_forward_return(close, horizon=self.horizon)
        df = pd.concat([X, y.rename("y")], axis=1).dropna()
        if len(df) < self.train_size + self.step:
            return pd.Series(dtype=float)

        preds = pd.Series(index=df.index, dtype=float, name="proba")
        for start in range(0, len(df) - self.train_size, self.step):
            train = df.iloc[start : start + self.train_size]
            test = df.iloc[start + self.train_size : start + self.train_size + self.step]
            if test.empty:
                break
            model = build_model(self.model_kind, **self.model_kwargs)
            model.fit(train.drop(columns=["y"]).to_numpy(), train["y"].to_numpy())
            proba = model.predict_proba(test.drop(columns=["y"]).to_numpy())[:, 1]
            preds.loc[test.index] = proba
        return preds.dropna()
