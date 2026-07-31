"""Stock diagnosis HTML report — K-line chart + indicators + verdict.

Renders a self-contained HTML file: candlestick chart with MA overlays and a
volume sub-chart, a score gauge, the indicator snapshot, fired signals, fund
flow / valuation (when available) and risk notes.
"""
from __future__ import annotations

import io
import base64
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti TC", "STHeiti", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from ..utils import ensure_dir, get_logger
from .diagnosis import DiagnosisResult
from .indicators import add_all_indicators

log = get_logger("StockReport")

COLOR_UP = "#d62728"   # red  (CN convention)
COLOR_DOWN = "#2ca02c"  # green


def _candlestick(df: pd.DataFrame, ax) -> None:
    width = 0.6
    for i in range(len(df)):
        row = df.iloc[i]
        o, c, h, l = row["open"], row["close"], row["high"], row["low"]
        color = COLOR_UP if c >= o else COLOR_DOWN
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
        body_lo = min(o, c)
        body_hi = max(o, c)
        ax.bar(i, body_hi - body_lo, bottom=body_lo, width=width,
               color=color, edgecolor=color, zorder=2)


def plot_kline(df: pd.DataFrame, name: str, n: int = 60):
    """Return (equity_fig) — candlestick + MA + volume."""
    plot_df = df.tail(n).copy()
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    _candlestick(plot_df, ax1)
    for col, color, lbl in [("ma5", "#1f77b4", "MA5"),
                            ("ma20", "#ff7f0e", "MA20"),
                            ("ma60", "#9467bd", "MA60")]:
        if col in plot_df.columns:
            ax1.plot(range(len(plot_df)), plot_df[col].values,
                     color=color, linewidth=1.1, label=lbl)
    ax1.set_title(f"{name} · 近{len(plot_df)}日K线", fontsize=12, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylabel("价格")

    colors = [COLOR_UP if c >= o else COLOR_DOWN
              for c, o in zip(plot_df["close"], plot_df["open"])]
    ax2.bar(range(len(plot_df)), plot_df["volume"].values, color=colors, width=0.6)
    ax2.set_ylabel("成交量")
    ax2.grid(True, alpha=0.3)

    step = max(1, len(plot_df) // 8)
    ticks = list(range(0, len(plot_df), step))
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([plot_df.index[i].strftime("%m-%d") for i in ticks], rotation=0, fontsize=9)
    fig.tight_layout()
    return fig


def _fig_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _score_color(score: int) -> str:
    if score >= 60:
        return "#d62728"
    if score >= 40:
        return "#7f7f7f"
    return "#2ca02c"


class StockReport:
    def __init__(self, result: DiagnosisResult, df: Optional[pd.DataFrame] = None) -> None:
        self.r = result
        self.df = df

    # ------------------------------------------------------------------ #
    def _signals_html(self) -> str:
        if not self.r.signals:
            return "<p class='muted'>近期无明显形态信号</p>"
        rows = ""
        for s in self.r.signals:
            color = "#d62728" if s.get("type") == "bull" else "#2ca02c" if s.get("type") == "bear" else "#7f7f7f"
            rows += (f"<tr><td style='color:{color}'>{s['name']}</td>"
                     f"<td>{s.get('detail','')}</td></tr>")
        return f"<table class='grid'><tbody>{rows}</tbody></table>"

    def _indicators_html(self) -> str:
        items = self.r.indicators
        rows = ""
        for k, v in items.items():
            val = "—" if v is None else f"{v}"
            rows += f"<tr><td class='k'>{k}</td><td class='v'>{val}</td></tr>"
        # 2-column layout: split in half
        half = (len(items) + 1) // 2
        left = "".join(f"<tr><td class='k'>{k}</td><td class='v'>{items[k] if items[k] is not None else '—'}</td></tr>"
                       for k in list(items)[:half])
        right = "".join(f"<tr><td class='k'>{k}</td><td class='v'>{items[k] if items[k] is not None else '—'}</td></tr>"
                        for k in list(items)[half:])
        return (f"<table class='grid'>{left}</table>"
                f"<table class='grid'>{right}</table>")

    def _risks_html(self) -> str:
        if not self.r.risks:
            return "<p class='muted'>无</p>"
        return "<ul>" + "".join(f"<li>{r}</li>" for r in self.r.risks) + "</ul>"

    # ------------------------------------------------------------------ #
    def to_html(self, path: str | Path) -> Path:
        r = self.r
        kline_img = ""
        if self.df is not None and not self.df.empty:
            df = add_all_indicators(self.df) if "ma5" not in self.df.columns else self.df
            kline_img = _fig_b64(plot_kline(df, f"{r.name}({r.code})"))

        score_color = _score_color(r.score)
        ff_html = self._kv(r.fund_flow, [("direction", "方向"), ("net_5d", "5日净额"), ("latest", "最新")])
        val_html = self._kv(r.valuation, [("pe_ttm", "PE(TTM)"), ("pb", "PB")])

        html = _TPL.format(
            title=f"{r.name}({r.code}) 个股诊断报告",
            name=r.name, code=r.code, market=r.market,
            price=r.price, change=r.change_pct,
            change_color="#d62728" if r.change_pct >= 0 else "#2ca02c",
            score=r.score, rating=r.rating, score_color=score_color,
            trend=r.trend, summary=r.summary,
            kline_img=kline_img,
            signals=self._signals_html(),
            indicators=self._indicators_html(),
            risks=self._risks_html(),
            fund_flow=ff_html, valuation=val_html,
        )
        path = ensure_dir(Path(path).parent) / Path(path).name
        path.write_text(html, encoding="utf-8")
        log.info("报告已保存: %s", path)
        return path

    @staticmethod
    def _kv(d, pairs):
        if not d:
            return "<p class='muted'>暂无数据</p>"
        rows = "".join(f"<tr><td class='k'>{lbl}</td><td class='v'>{d.get(k, '—')}</td></tr>"
                       for k, lbl in pairs)
        return f"<table class='grid'>{rows}</table>"


_TPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family:-apple-system,"PingFang SC","Segoe UI",sans-serif;
         margin:24px; background:#f7f8fa; color:#1f2933; }}
  h1 {{ font-size:20px; border-bottom:2px solid #1f77b4; padding-bottom:6px; }}
  h2 {{ font-size:15px; margin-top:24px; color:#1f77b4; }}
  .head {{ display:flex; gap:32px; align-items:center; flex-wrap:wrap; }}
  .gauge {{ width:120px; height:120px; border-radius:50%;
            background:conic-gradient({score_color} {score}% , #e5e7eb 0);
            display:flex; align-items:center; justify-content:center; }}
  .gauge .inner {{ width:92px; height:92px; border-radius:50%; background:#fff;
            display:flex; flex-direction:column; align-items:center; justify-content:center; }}
  .gauge .num {{ font-size:24px; font-weight:600; color:{score_color}; }}
  .gauge .lbl {{ font-size:11px; color:#6b7280; }}
  .rating {{ font-size:20px; font-weight:600; color:{score_color}; }}
  .price {{ font-size:22px; font-weight:600; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; background:#fff;
           box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  td,th {{ border:1px solid #e5e7eb; padding:6px 10px; }}
  td.k {{ background:#f0f4f8; font-weight:600; width:45%; }}
  td.v {{ font-family:"SF Mono",monospace; }}
  .chart {{ background:#fff; padding:8px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .chart img {{ width:100%; }}
  .muted {{ color:#6b7280; font-size:13px; }}
  ul {{ margin:6px 0; padding-left:20px; }}
  li {{ font-size:13px; margin:3px 0; }}
  .summary {{ background:#fff; border-left:4px solid {score_color};
              padding:12px 16px; font-size:14px; line-height:1.7;
              box-shadow:0 1px 3px rgba(0,0,0,.08); }}
</style></head>
<body>
<h1>{name} <span style="font-size:14px;color:#6b7280">({code}) · {market}</span></h1>
<div class="head">
  <div class="gauge"><div class="inner"><div class="num">{score}</div><div class="lbl">/100</div></div></div>
  <div>
    <div class="rating">评级：{rating}</div>
    <div style="margin-top:6px">趋势：<b>{trend}</b></div>
    <div style="margin-top:6px" class="price">{price} <span style="font-size:14px;color:{change_color}">{change:+.2f}%</span></div>
  </div>
</div>
<div class="summary" style="margin-top:16px">{summary}</div>
<div class="chart"><img src="data:image/png;base64,{kline_img}"></div>
<div class="grid2">
  <div><h2>技术指标</h2>{indicators}</div>
  <div><h2>形态信号</h2>{signals}</div>
</div>
<div class="grid2">
  <div><h2>资金面</h2>{fund_flow}</div>
  <div><h2>估值面</h2>{valuation}</div>
</div>
<h2>风险提示</h2>
{risks}
</body></html>"""
