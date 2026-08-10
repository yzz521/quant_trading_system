"""vibe_format 抽取逻辑离线单测（基于 Vibe 实际输出格式）。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.vibe_format import (
    build_display_summary,
    is_process_draft,
)


FIXTURE = """## ① 一段总括

该组合以 1 万元本金运行，仓位利用率 95.7%，已是事实满仓。银行三兄弟占 74%，
金融集中度极高，整体浮亏约 158 元。第一要务是先回答三个结构问题：
单票超标、金融集中度过高、满仓零操作空间。

## ② 按标的分条

**招商银行（-1.97%）——组合最大的风险点不是亏损，是集中度**
- 观点：浮亏约 78 元，绝对额不大，但成本占本金 39.6%，突破自身 30% 上限。
- 风险：三只银行股同涨同跌，个股分散实为伪分散；招行一票占比近四成。

**工商银行（-5.58%）——最深的浮亏，也是最典型的"需观察而非行动"**
- 观点：浮亏约 89 元，是组合绝对亏损最大的标的。
- 风险：真正要观察的是银行板块整体趋势而非工行个股。

| 代码 | 名称 | 数量 | 成本价 | 现价 | pnl_pct | 成本额(元) | 占本金 |
|---|---|---|---|---|---|---|---|
| 600036 | 招商银行 | 100 | 39.58 | 38.80 | -1.97% | 3,958 | 39.6% |
| 600000 | 浦发银行 | 200 | 9.365 | 9.21 | -1.66% | 1,873 | 18.7% |

**Candidates 扫描命中（15 只）**

| 代码 | 名称 | 得分 | 命中因子 | 现价(JSON close) | 当日涨幅 |
|---|---|---|---|---|---|
| 603159 | 上海亚虹 | 50 | 突破新高+放量 | 20.54 | +7.26% |

## ③ 三条纪律提醒

1. **满仓即无选择权。** 95.7% 仓位已无加仓能力，一切新增动作只能是卖 A 买 B。
2. **深套处理以逻辑为锚、不以价格为锚。** 最大浮亏仅 -5.58%，不存在需要解套的深套。
3. **扫描命中 ≠ 买入信号。** 追高是满仓组合最贵的错误。

## ④ 免责声明

本点评仅为研究性二次分析，非投资建议。未校验实时行情。
"""


def test_real_format_extraction():
    d = build_display_summary(FIXTURE)
    assert d["partial"] is False
    assert d["overview"].startswith("该组合以 1 万元本金")
    assert len(d["risks"]) >= 2
    assert len(d["disciplines"]) == 3
    assert "满仓即无选择权" in d["disciplines"][0]
    codes = {s["code"] for s in d["symbols"]}
    assert codes == {"600036", "600000"}  # 候选表不混入
    assert d["symbols"][0]["pnl"] == "-1.97%"
    assert "【总括】该组合以 1 万元本金" in d["clean_summary"]
    assert "600036 招商银行" in d["clean_summary"]
    assert d["fallback_raw"] is False


def test_discipline_fragment_not_used_as_risk():
    text = FIXTURE.replace(
        "1. **满仓即无选择权。**",
        "1. **满仓即无选择权。** 换仓之前应先恢复三项结构纪律：单票 ≤ 30%（招行现超标）、金融集中度上限、以及先于新标的确定换仓资金来源。",
    )
    d = build_display_summary(text)
    assert d["overview"].startswith("该组合以 1 万元本金")
    assert all("招行现超标）、金融集中度上限" not in r for r in d["risks"])
    assert "招行现超标）、金融集中度上限" not in d["overview"]
    # 碎片出现在纪律提醒正文里属于正常上下文，不应出现在总括/风险位
    assert "【总括】招行现超标）、金融集中度上限" not in d["clean_summary"]
    assert "【风险要点】\n· 招行现超标）、金融集中度上限" not in d["clean_summary"]


def test_raw_fallback_when_structure_unparsable():
    raw = "这是一段很长的普通文本。" * 40
    d = build_display_summary(raw)
    assert d["fallback_raw"] is True
    assert d["clean_summary"] == raw


def test_process_draft_detected():
    assert is_process_draft("## Goal\n...\n## Progress\n尚未输出终稿")
    assert not is_process_draft(FIXTURE)
