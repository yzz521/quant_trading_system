"""LLM 兜底（OpenAI 兼容）离线单测：monkeypatch chat_completion，不打网络。"""
from __future__ import annotations

from quant_trading_system.stock_analysis import ai_client
from quant_trading_system.stock_analysis.vibe_bridge import submit_llm_analysis


def _payload() -> dict:
    return {
        "source": "gp_assistant",
        "market": "CN",
        "holdings": [{
            "code": "600000", "name": "浦发银行", "quantity": 200,
            "cost_price": 9.365, "current_price": 9.21, "pnl_pct": -1.66,
        }],
        "candidates": [{"code": "600050", "name": "中国联通", "score": 45}],
    }


def test_submit_llm_analysis_ok(tmp_path, monkeypatch):
    text = (
        "## ① 一段总括\n\n组合高仓位高集中。\n\n"
        "## ② 按标的分条\n\n**招商银行**\n- 风险：集中度超标，占比近四成。\n\n"
        "## ③ 三条纪律提醒\n\n1. 满仓即无选择权。"
    )
    monkeypatch.setattr(ai_client, "chat_completion", lambda *a, **k: text)
    res = submit_llm_analysis(
        _payload(), root=tmp_path, api_key="sk-test",
        base_url="https://api.deepseek.com", model="deepseek-chat",
    )
    assert res["ok"] is True
    assert res["source"] == "llm"
    assert res["summary"] == text
    assert "【总括】组合高仓位高集中" in res["clean_summary"]
    assert (tmp_path / "results" / "vibe").exists()


def test_submit_llm_analysis_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_client, "chat_completion", lambda *a, **k: None)
    res = submit_llm_analysis(_payload(), root=tmp_path, api_key="sk-test")
    assert res["ok"] is False
    assert "LLM 兜底" in (res.get("error") or "")
