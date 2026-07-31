"""Machine-learning strategy.

Builds technical features from the rolling bar buffer, labels each bar with
the sign of the next-bar return, and trains a RandomForest classifier. Once
trained it predicts every bar; a positive prediction enters long, a negative
prediction exits. The model retrains on a rolling window so the strategy
adapts to regime changes.

Heavy lifting is intentionally minimal — this is a template. Swap the model,
feature set or label horizon for your own research.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core import Bar, Direction
from .base import Strategy
from .multi_factor import _rsi


class MLStrategy(Strategy):
    """Random-forest next-bar direction classifier.

    Params
    ------
    train_size : int    minimum bars before first training (default 200)
    retrain_every : int retrain cadence in bars (default 50)
    """

    def __init__(self, symbols, train_size: int = 200, retrain_every: int = 50,
                 name: str = "ML_RF", **kw):
        super().__init__(symbols, name=name, train_size=train_size,
                         retrain_every=retrain_every, **kw)
        self.train_size = train_size
        self.retrain_every = retrain_every
        self._model = None
        self._bar_count = 0
        self._last_features: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------ #
    def _features(self, closes: pd.Series) -> np.ndarray | None:
        if len(closes) < 25:
            return None
        ret1 = closes.pct_change(1)
        ret5 = closes.pct_change(5)
        ret20 = closes.pct_change(20)
        vol20 = ret1.rolling(20).std()
        rsi = closes.rolling(15).apply(lambda s: _rsi(s, 14), raw=False)
        ma5 = closes.rolling(5).mean()
        ma20 = closes.rolling(20).mean()
        feat = pd.DataFrame({
            "ret1": ret1, "ret5": ret5, "ret20": ret20,
            "vol20": vol20, "rsi": rsi,
            "ma_bias": (closes - ma20) / ma20,
            "ma_cross": (ma5 - ma20) / ma20,
        })
        feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
        if feat.empty:
            return None
        return feat.iloc[-1].to_numpy(dtype=float)

    def _build_dataset(self, closes: pd.Series):
        ret1 = closes.pct_change(1)
        ret5 = closes.pct_change(5)
        ret20 = closes.pct_change(20)
        vol20 = ret1.rolling(20).std()
        rsi = closes.rolling(15).apply(lambda s: _rsi(s, 14), raw=False)
        ma5 = closes.rolling(5).mean()
        ma20 = closes.rolling(20).mean()
        feat = pd.DataFrame({
            "ret1": ret1, "ret5": ret5, "ret20": ret20,
            "vol20": vol20, "rsi": rsi,
            "ma_bias": (closes - ma20) / ma20,
            "ma_cross": (ma5 - ma20) / ma20,
        })
        # Label: sign of next-bar return
        feat["label"] = (closes.shift(-1) > closes).astype(int)
        feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
        if len(feat) < self.train_size:
            return None, None
        X = feat.drop(columns=["label"]).to_numpy()
        y = feat["label"].to_numpy()
        return X, y

    def _train(self, symbol: str) -> None:
        closes = self.to_series(symbol, "close")
        X, y = self._build_dataset(closes)
        if X is None:
            return
        try:
            from sklearn.ensemble import RandomForestClassifier  # type: ignore
        except ImportError:
            self.log.warning("scikit-learn not installed; ML strategy disabled.")
            return
        self._model = RandomForestClassifier(
            n_estimators=100, max_depth=5, random_state=42, class_weight="balanced"
        )
        self._model.fit(X, y)
        self.log.info("Model trained on %s: %d samples", symbol, len(y))

    # ------------------------------------------------------------------ #
    def on_bar(self, bar: Bar) -> None:
        self._bar_count += 1
        closes = self.to_series(bar.symbol, "close")

        # (Re)train
        if self._model is None and len(closes) >= self.train_size:
            self._train(bar.symbol)
        elif self._model is not None and self._bar_count % self.retrain_every == 0:
            self._train(bar.symbol)

        if self._model is None:
            return

        feats = self._features(closes)
        if feats is None:
            return
        pred = self._model.predict(feats.reshape(1, -1))[0]
        pos = self.position(bar.symbol)
        if pred == 1 and pos <= 0:
            self.emit_signal(bar.symbol, Direction.LONG, strength=1.0)
        elif pred == 0 and pos > 0:
            self.emit_signal(bar.symbol, Direction.EXIT, strength=1.0)
