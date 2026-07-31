"""Self-contained HTML performance report.

Renders metrics, equity/drawdown charts, monthly returns, open positions and
the trade log into a single ``.html`` file that opens in any browser — no
external assets required (charts are embedded as base64 PNGs).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from ..portfolio import Portfolio
from ..utils import get_logger, ensure_dir
from .metrics import compute_metrics
from .plot import plot_equity_drawdown, plot_monthly_returns, fig_to_base64


def _fmt(v, pct=False, money=False, digits=4) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if pct:
        return f"{v*100:.2f}%"
    if money:
        return f"{v:,.2f}"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


class PerformanceReport:
    def __init__(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        self.metrics = compute_metrics(portfolio)
        self.log = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    def _metrics_table(self) -> str:
        m = self.metrics
        rows = [
            ("Initial Capital", _fmt(m.get("initial_capital"), money=True)),
            ("Final Equity", _fmt(m.get("final_equity"), money=True)),
            ("Total Return", _fmt(m.get("total_return"), pct=True)),
            ("Annual Return", _fmt(m.get("annual_return"), pct=True)),
            ("Annual Volatility", _fmt(m.get("annual_volatility"), pct=True)),
            ("Sharpe Ratio", _fmt(m.get("sharpe"))),
            ("Sortino Ratio", _fmt(m.get("sortino"))),
            ("Calmar Ratio", _fmt(m.get("calmar"))),
            ("Max Drawdown", _fmt(m.get("max_drawdown"), pct=True)),
            ("Max DD Duration (days)", _fmt(m.get("max_drawdown_duration"))),
            ("Trading Days", _fmt(m.get("n_trading_days"))),
            ("Orders Filled", _fmt(m.get("n_orders"))),
            ("Closed Trades", _fmt(m.get("n_trades"))),
            ("Win Rate", _fmt(m.get("win_rate"), pct=True)),
            ("Profit Factor", _fmt(m.get("profit_factor"))),
            ("Avg Win", _fmt(m.get("avg_win"), money=True)),
            ("Avg Loss", _fmt(m.get("avg_loss"), money=True)),
            ("Expectancy / Trade", _fmt(m.get("expectancy"), money=True)),
            ("Total Realized PnL", _fmt(m.get("total_realized_pnl"), money=True)),
            ("Daily Win Rate", _fmt(m.get("daily_win_rate"), pct=True)),
        ]
        body = "".join(
            f"<tr><td class='k'>{k}</td><td class='v'>{v}</td></tr>" for k, v in rows
        )
        return f"<table class='metrics'><tbody>{body}</tbody></table>"

    def _positions_table(self) -> str:
        df = self.portfolio.positions_frame()
        if df.empty:
            return "<p class='muted'>No open positions.</p>"
        df = df.reset_index()
        head = "".join(f"<th>{c}</th>" for c in df.columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>"
            for row in df.itertuples(index=False, name=None)
        )
        return f"<table class='grid'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def _trades_table(self, limit: int = 20) -> str:
        trades = self.portfolio.trades[-limit:]
        if not trades:
            return "<p class='muted'>No closed trades.</p>"
        df = pd.DataFrame(trades)
        cols = ["symbol", "side", "quantity", "entry_price", "exit_price", "pnl", "exit_time"]
        df = df[[c for c in cols if c in df.columns]]
        head = "".join(f"<th>{c}</th>" for c in df.columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>"
            for row in df.itertuples(index=False, name=None)
        )
        return f"<table class='grid'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    # ------------------------------------------------------------------ #
    def to_html(self, path: str | Path, title: str = "Quant Backtest Report") -> Path:
        eq = self.portfolio.equity_curve_frame()
        eq_img = fig_to_base64(plot_equity_drawdown(eq)) if not eq.empty else ""
        mo_img = fig_to_base64(plot_monthly_returns(eq)) if not eq.empty else ""

        html = _HTML_TEMPLATE.format(
            title=title,
            metrics=self._metrics_table(),
            equity_img=eq_img,
            monthly_img=mo_img,
            positions=self._positions_table(),
            trades=self._trades_table(),
        )
        path = ensure_dir(Path(path).parent) / Path(path).name
        path.write_text(html, encoding="utf-8")
        self.log.info("Report saved to %s", path)
        return path

    def to_dict(self) -> dict:
        return self.metrics

    def print_summary(self) -> None:
        m = self.metrics
        self.log.info("=" * 60)
        self.log.info("Final Equity: %.2f | Total Return: %.2f%% | Sharpe: %.2f",
                      m.get("final_equity", 0), m.get("total_return", 0) * 100,
                      m.get("sharpe", 0))
        self.log.info("Max Drawdown: %.2f%% | Trades: %d | Win Rate: %.2f%%",
                      m.get("max_drawdown", 0) * 100, m.get("n_trades", 0),
                      m.get("win_rate", 0) * 100)
        self.log.info("=" * 60)


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:,.4f}"
    return str(v)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Segoe UI", sans-serif;
         margin: 24px; background: #f7f8fa; color: #1f2933; }}
  h1 {{ font-size: 20px; border-bottom: 2px solid #1f77b4; padding-bottom: 6px; }}
  h2 {{ font-size: 15px; margin-top: 28px; color: #1f77b4; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px;
           background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  td, th {{ border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }}
  th {{ background: #f0f4f8; font-weight: 600; }}
  table.metrics td.k {{ background: #f0f4f8; font-weight: 600; width: 55%; }}
  table.metrics td.v {{ font-family: "SF Mono", monospace; }}
  .chart {{ background:#fff; padding:8px; box-shadow:0 1px 3px rgba(0,0,0,0.08); }}
  .chart img {{ width: 100%; }}
  .muted {{ color:#6b7280; font-size:13px; }}
  .footer {{ margin-top:28px; color:#9ca3af; font-size:11px; }}
</style></head>
<body>
<h1>{title}</h1>
<div class="grid">
  <div><h2>Performance Metrics</h2>{metrics}</div>
  <div><h2>Open Positions</h2>{positions}</div>
</div>
<h2>Equity Curve &amp; Drawdown</h2>
<div class="chart"><img src="data:image/png;base64,{equity_img}"></div>
<h2>Monthly Returns</h2>
<div class="chart"><img src="data:image/png;base64,{monthly_img}"></div>
<h2>Recent Trades</h2>
{trades}
<div class="footer">Generated by quant_trading_system · charts follow CN convention (red=up, green=down)</div>
</body></html>"""
