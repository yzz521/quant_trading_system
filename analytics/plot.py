"""Matplotlib visualisations for the equity curve, drawdown and monthly returns.

Uses the Agg backend so charts can be rendered headless and embedded into the
HTML report. Colours follow the Chinese convention where applicable
(red = up, green = down).
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

# Chinese market convention: red for gains, green for losses.
COLOR_UP = "#d62728"   # red
COLOR_DOWN = "#2ca02c"  # green
COLOR_NAVY = "#1f77b4"


def plot_equity_drawdown(eq: pd.DataFrame) -> Figure:
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    equity = eq["equity"]
    ax1.plot(equity.index, equity.values, color=COLOR_NAVY, linewidth=1.4)
    ax1.fill_between(equity.index, equity.values, equity.iloc[0],
                     color=COLOR_NAVY, alpha=0.12)
    ax1.set_title("Equity Curve", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Equity")
    ax1.grid(True, alpha=0.3)

    cummax = equity.cummax()
    drawdown = equity / cummax - 1.0
    ax2.fill_between(drawdown.index, drawdown.values, 0, color=COLOR_DOWN, alpha=0.5)
    ax2.plot(drawdown.index, drawdown.values, color=COLOR_DOWN, linewidth=0.8)
    ax2.set_title("Drawdown", fontsize=10)
    ax2.set_ylabel("Drawdown")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.tight_layout()
    return fig


def plot_monthly_returns(eq: pd.DataFrame) -> Figure:
    monthly = eq["return"].resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly.index = monthly.index.to_period("M").to_timestamp()
    fig, ax = plt.subplots(figsize=(11, 3.2))
    colors = [COLOR_UP if v >= 0 else COLOR_DOWN for v in monthly.values]
    ax.bar(monthly.index, monthly.values * 100, color=colors, width=20)
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_title("Monthly Returns (%)", fontsize=11, fontweight="bold")
    ax.set_ylabel("%")
    ax.grid(True, alpha=0.3, axis="y")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.tight_layout()
    return fig


def fig_to_base64(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    import base64
    return base64.b64encode(buf.getvalue()).decode("ascii")
