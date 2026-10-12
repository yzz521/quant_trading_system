"""调度器把「K 线不足」和真正的扫描失败分成两行日志。

次新股根数不够是预期内的跳过（INFO，带代码、名称、原因、实际根数）。
异常仍是失败（WARNING，带代码、名称和错误信息）。归类看扫描结果上的
``skipped`` / ``failed``，不看错误文案里有没有「数据不足」。
"""
from __future__ import annotations

import logging

import pandas as pd
from quant_trading_system.utils import save_yaml


def _make_sched(tmp_path):
    from quant_trading_system.stock_analysis.scheduler import MarketScheduler

    cfg = {
        "enabled_markets": ["CN"],
        "stock_pools": {"CN": ["600519"]},
        "notify": {"email": {"enabled": False}},
        "schedule": {"poll_interval_sec": 60},
        "opportunity": {"enabled": True, "max_stocks": 5, "workers": 1},
    }
    path = tmp_path / "notify.yaml"
    save_yaml(path, cfg)
    return MarketScheduler(str(path))


class _SilentEngine:
    """数据不足 / 加载异常路径不应进入打分。"""

    def analyze(self, code, name, df, extra=None):  # noqa: ARG002
        raise AssertionError(f"不应调用引擎: {code}")


def _run(tmp_path, monkeypatch, candidates, loader):
    """跑一轮真实扫描器，外部行情全部换成桩，返回扫描结果。"""
    from quant_trading_system.stock_analysis import scheduler as S
    from quant_trading_system.stock_analysis.opportunity.batch_scanner import (
        OpportunityBatchScanner,
    )

    sched = _make_sched(tmp_path)
    monkeypatch.setattr(sched.holdings, "compute_pnl", lambda market=None: ([], {}))
    monkeypatch.setattr(sched.holdings, "capital_snapshot", lambda: None)
    monkeypatch.setattr(sched.holdings, "get_account", lambda: {"total_capital": 0})
    monkeypatch.setattr(S, "analyze_holding_actions", lambda h: [])
    monkeypatch.setattr(sched, "_holdings_quant_for_market", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_record_equity_daily", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_track_paper", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_shadow_tick", lambda *a, **k: None)
    monkeypatch.setattr(S, "build_market_message", lambda *a, **k: ("标题", "正文", "<p></p>"))

    class _Notifier:
        def send(self, *a, **k):  # noqa: ARG002
            return None

    monkeypatch.setattr(sched, "notifier", _Notifier())
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.market.regime_for_market",
        lambda *a, **k: (50.0, 1.0),
    )
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.sector.fetch_sector_rank",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.sector.get_stock_sectors",
        lambda *a, **k: {},
    )
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.screener.screen_candidates",
        lambda *a, **k: list(candidates),
    )

    captured: dict = {}

    def factory(**kwargs):
        scanner = OpportunityBatchScanner(
            engine=_SilentEngine(),
            loader=loader,
            workers=1,
            fetch_fundamentals=False,
            min_opportunity_score=float(kwargs.get("min_opportunity_score", 0.0)),
            min_stock_score=float(kwargs.get("min_stock_score", 0.0)),
        )
        real_scan = scanner.scan

        def scan(*a, **k):
            res = real_scan(*a, **k)
            captured["res"] = res
            return res

        scanner.scan = scan
        return scanner

    monkeypatch.setattr(S, "OpportunityBatchScanner", factory)
    sched._run_market("CN")
    return captured["res"]


def _messages(caplog, level, fragment):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "Scheduler" and r.levelno == level and fragment in r.getMessage()
    ]


class TestSchedulerScanLog:
    def test_insufficient_kline_logged_as_skip(self, tmp_path, monkeypatch, caplog):

        def loader(code, name):  # noqa: ARG001
            return pd.DataFrame({"close": [1.0] * 23})

        with caplog.at_level(logging.INFO, logger="Scheduler"):
            res = _run(
                tmp_path, monkeypatch,
                [{"code": "688825", "name": "某某股份"}],
                loader,
            )

        assert res.failed == []
        assert len(res.skipped) == 1
        assert res.skipped[0]["status"] == "skipped"
        assert res.skipped[0]["bar_count"] == 23
        assert res.skipped[0]["name"] == "某某股份"

        skips = _messages(caplog, logging.INFO, "机会扫描跳过")
        assert skips == [
            "[CN] 机会扫描跳过 1 只（数据不足）: 688825 某某股份 K线 23 根 < 60"
        ]
        assert _messages(caplog, logging.WARNING, "机会扫描") == []
        assert "V2 机会扫描跳过" not in caplog.text

    def test_exception_logged_as_failure_with_error(self, tmp_path, monkeypatch, caplog):

        def loader(code, name):  # noqa: ARG001
            raise RuntimeError("行情接口超时")

        with caplog.at_level(logging.INFO, logger="Scheduler"):
            res = _run(
                tmp_path, monkeypatch,
                [{"code": "600000", "name": "浦发银行"}],
                loader,
            )

        assert res.skipped == []
        assert len(res.failed) == 1
        assert res.failed[0]["status"] == "failed"
        assert res.failed[0]["error"] == "行情接口超时"
        assert res.failed[0]["name"] == "浦发银行"

        fails = _messages(caplog, logging.WARNING, "机会扫描")
        assert fails == ["[CN] 机会扫描 1 只失败: 600000 浦发银行: 行情接口超时"]
        assert _messages(caplog, logging.INFO, "机会扫描跳过") == []

    def test_mixed_skip_and_failure(self, tmp_path, monkeypatch, caplog):

        def loader(code, name):  # noqa: ARG001
            if code == "688825":
                return pd.DataFrame({"close": [1.0] * 23})
            raise RuntimeError("行情接口超时")

        with caplog.at_level(logging.INFO, logger="Scheduler"):
            res = _run(
                tmp_path, monkeypatch,
                [
                    {"code": "688825", "name": "某某股份"},
                    {"code": "600000", "name": "浦发银行"},
                ],
                loader,
            )

        assert [s["code"] for s in res.skipped] == ["688825"]
        assert res.skipped[0]["bar_count"] == 23
        assert res.skipped[0]["status"] == "skipped"
        assert [f["code"] for f in res.failed] == ["600000"]
        assert res.failed[0]["status"] == "failed"
        assert "行情接口超时" in res.failed[0]["error"]

        assert _messages(caplog, logging.INFO, "机会扫描跳过") == [
            "[CN] 机会扫描跳过 1 只（数据不足）: 688825 某某股份 K线 23 根 < 60"
        ]
        assert _messages(caplog, logging.WARNING, "机会扫描") == [
            "[CN] 机会扫描 1 只失败: 600000 浦发银行: 行情接口超时"
        ]
