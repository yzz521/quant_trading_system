"""Vibe 载荷 / 扫描候选落盘离线单测（不打网络）。"""
from __future__ import annotations

import json

from quant_trading_system.stock_analysis.vibe_bridge import (
    PROMPT_PREFIX,
    _fit_prompt,
    build_payload,
    load_latest_scan,
    save_latest_scan,
)


def test_build_payload_candidates_fields():
    candidates = [
        {
            "code": "600050",
            "name": "中国联通",
            "score": 45,
            "matched": ["多头排列(新晋)"],
            "matched_days": {"多头排列(新晋)": 2},
            "close": 4.38,
            "change_pct": -0.23,
            "market_cap": 1369.38,
            "pe": 15.82,
            "news_risks": [{"title": "x", "keywords": ["减持"]}],
        },
        {"code": "000001", "name": "平安银行"},  # 缺字段不报错
    ]
    payload = build_payload(holdings=[], candidates=candidates)
    c0, c1 = payload["candidates"]
    assert c0["code"] == "600050"
    assert c0["close"] == 4.38
    assert c0["market_cap"] == 1369.38
    assert c0["matched_days"]["多头排列(新晋)"] == 2
    assert c0["news_risks"][0]["keywords"] == ["减持"]
    assert c1["code"] == "000001"
    assert c1.get("close") is None  # 缺字段保持 None


def test_latest_scan_roundtrip(tmp_path):
    hits = [
        {"code": "600050", "name": "中国联通", "score": 45},
        {"code": "000001", "name": "平安银行", "score": 30},
        {"code": "300750", "name": "宁德时代", "score": 20},
    ]
    path = save_latest_scan(tmp_path, hits, market="CN", limit=2)
    assert path.exists()
    data = load_latest_scan(tmp_path)
    assert data["market"] == "CN"
    assert len(data["hits"]) == 2  # limit 生效
    assert data["hits"][0]["code"] == "600050"


def test_load_latest_scan_missing(tmp_path):
    data = load_latest_scan(tmp_path)
    assert data["hits"] == []
    assert data["as_of"] == ""


def _big_payload() -> dict:
    return {
        "source": "gp_assistant",
        "market": "CN",
        "holdings": [{"code": "600000", "name": "浦发银行", "quantity": 100, "cost_price": 10.0}],
        "candidates": [
            {
                "code": f"6000{i:02d}",
                "name": "测试股票",
                "score": 50,
                "matched": ["MACD金叉", "放量突破", "多头排列(新晋)"],
                "signals": ["条件说明" * 40],
                "market_cap": 100.0,
                "pe": 15.0,
                "turnover": 2.5,
                "main_net": 1234.5,
                "news_risks": [{"title": "x" * 60, "keywords": ["减持"]}],
            }
            for i in range(15)
        ],
    }


def test_fit_prompt_large_payload_within_limit():
    prompt = _fit_prompt(_big_payload(), PROMPT_PREFIX)
    assert len(prompt) <= 4700  # Vibe content 上限 5000，留余量
    assert prompt.startswith(PROMPT_PREFIX)
    # 大载荷不会原样全量塞入
    assert len(prompt) < 3000


def test_fit_prompt_small_payload_roundtrip():
    payload = {
        "source": "gp_assistant",
        "holdings": [{"code": "600000", "quantity": 100}],
        "candidates": [],
    }
    prompt = _fit_prompt(payload, PROMPT_PREFIX)
    assert len(prompt) < 4700
    assert json.loads(prompt[len(PROMPT_PREFIX):]) == payload
