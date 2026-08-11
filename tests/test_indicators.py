"""指标文字解读单测（移植自 ashare-analyzer 的解释逻辑）。"""
from __future__ import annotations

from quant_trading_system.stock_analysis.indicators import (
    explain_indicators,
    interpret_adx,
    interpret_macd,
    interpret_rsi,
    interpret_stochastic,
)


def test_interpret_rsi():
    assert interpret_rsi(85) == "严重超买"
    assert interpret_rsi(72) == "超买"
    assert interpret_rsi(60) == "偏强"
    assert interpret_rsi(35) == "偏弱"
    assert interpret_rsi(25) == "超卖"
    assert interpret_rsi(10) == "严重超卖"


def test_interpret_others():
    assert interpret_stochastic(85, 82) == "超买"
    assert interpret_stochastic(15, 18) == "超卖"
    assert interpret_stochastic(60, 50) == "金叉偏多"
    assert interpret_macd(0.1) == "红柱（动能偏多）"
    assert interpret_macd(-0.1) == "绿柱（动能偏空）"
    assert interpret_adx(30) == "强趋势"
    assert interpret_adx(10) == "无趋势/震荡"


def test_explain_indicators_case_insensitive():
    row = {"RSI12": 70.0, "MACD_Hist": 0.12, "K": 80.0, "D": 75.0, "ADX": 26.0}
    out = explain_indicators(row)
    assert any("RSI" in x for x in out)
    assert any("MACD" in x for x in out)
    assert any("KDJ" in x for x in out)
    assert any("ADX" in x for x in out)
    assert explain_indicators({}) == []
