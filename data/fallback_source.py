"""Multi-source data fallback: try primary, then secondary sources in order.

Typical A-share setup::

    src = FallbackDataSource([
        AkShareSource(),
        # optional: local parquet via a thin wrapper, or yfinance for dual-listed
    ])
"""
from __future__ import annotations

from typing import Sequence

import pandas as pd

from ..utils import get_logger
from .data_source import AssetClass, DataSource


class FallbackDataSource(DataSource):
    name = "fallback"

    def __init__(
        self,
        sources: Sequence[DataSource],
        asset_class: AssetClass = AssetClass.EQUITY_CN,
    ) -> None:
        super().__init__(asset_class)
        if not sources:
            raise ValueError("FallbackDataSource requires at least one source")
        self.sources = list(sources)
        self.log = get_logger(self.__class__.__name__)
        self.last_source: str | None = None

    def get_history(
        self,
        symbol: str,
        start: str,
        end: str,
        frequency: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        errors: list[str] = []
        for src in self.sources:
            try:
                df = src.get_history(symbol, start, end, frequency=frequency, adjust=adjust)
                if df is not None and not df.empty:
                    self.last_source = getattr(src, "name", src.__class__.__name__)
                    self.log.info(
                        "Fallback hit %s via %s (%d rows)",
                        symbol,
                        self.last_source,
                        len(df),
                    )
                    return df
                errors.append(f"{getattr(src, 'name', src)}: empty")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{getattr(src, 'name', src)}: {e}")
                self.log.warning("Source %s failed for %s: %s", getattr(src, "name", src), symbol, e)
        self.log.error("All sources failed for %s: %s", symbol, errors)
        return pd.DataFrame()
