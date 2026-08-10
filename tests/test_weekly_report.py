"""周报代码收集 + 触发判断离线单测。"""
from __future__ import annotations

import json
from datetime import datetime

from quant_trading_system.stock_analysis.scheduler import _funnel_due, _weekly_due
from quant_trading_system.weekly_report import run as weekly_run


def test_collect_codes_holdings_plus_funnel(tmp_path):
    funnel = tmp_path / "results" / "latest_funnel.json"
    funnel.parent.mkdir(parents=True)
    funnel.write_text(json.dumps({
        "hits": [{"code": "600036"}, {"code": "600000"}, {"code": "600519"}],
    }), encoding="utf-8")
    codes = weekly_run.collect_codes(
        [{"code": "600000"}, {"code": "600104"}], root=tmp_path, top_n=2,
    )
    # 持仓优先；漏斗只取前 2（600036、600000 与持仓重复）
    assert codes == ["600000", "600104", "600036"]


def test_collect_codes_fallback_scan(tmp_path):
    scan = tmp_path / "results" / "latest_scan.json"
    scan.parent.mkdir(parents=True)
    scan.write_text(json.dumps({
        "hits": [{"code": "601111"}, {"code": "600050"}],
    }), encoding="utf-8")
    codes = weekly_run.collect_codes([], root=tmp_path, top_n=5)
    assert codes == ["601111", "600050"]


def test_collect_codes_empty(tmp_path):
    assert weekly_run.collect_codes([], root=tmp_path, top_n=5) == []


def test_weekly_due():
    fri = datetime(2026, 8, 14, 15, 31)  # 周五
    cfg = {"enabled": True, "time": "15:30"}
    assert _weekly_due(fri, "", cfg) is True
    assert _weekly_due(fri, "2026-08-14", cfg) is False  # 同日已生成
    assert _weekly_due(datetime(2026, 8, 14, 15, 29), "", cfg) is False  # 未到点
    assert _weekly_due(datetime(2026, 8, 13, 15, 31), "", cfg) is False  # 周四
    assert _weekly_due(fri, "", {"enabled": False, "time": "15:30"}) is False


def test_funnel_due():
    mon = datetime(2026, 8, 10, 15, 11)  # 周一
    cfg = {"enabled": True, "time": "15:10"}
    assert _funnel_due(mon, "", cfg) is True
    assert _funnel_due(mon, "2026-08-10", cfg) is False
    assert _funnel_due(datetime(2026, 8, 10, 15, 9), "", cfg) is False
    assert _funnel_due(datetime(2026, 8, 15, 15, 11), "", cfg) is False  # 周六
