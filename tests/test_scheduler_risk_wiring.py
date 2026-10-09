"""调度器 × 组合风控**接线**测试。

为什么单独开一个文件：``test_shadow_orders.py`` 钉死的是「5 道闸门各自逻辑对不对」，
而这里要钉死的是另一件事 —— **这些风控到底有没有被执行**。

真实缺陷（本次修复）：``scheduler.py`` 里

    portfolio_risk = portfolio_risk_block(holdings, total_equity=total_equity, ...)

的 ``total_equity`` **从未定义**，于是抛 ``NameError`` → 被外层
``except Exception`` 吞掉 → 组合风控（集中度 / 行业暴露 / 相关性 / VaR /
**回撤熔断 ``apply_brake_to_plans``**）**从未运行过一次**，日志里只留一行
warning。闸门写得再正确，没接线也等于零；而单元测试**测不出**这种缺陷 ——
被调用的函数完全正确，错的是「没被调用」。

所以这里的断言全部围绕「调用发生了吗、参数对吗」：

  1. ``portfolio_risk_block`` 确实被调用，且拿到的是**真实净值**（非 None）；
  2. 熔断系数确实传到了 ``apply_brake_to_plans``，并落到计划的建议仓位上；
  3. 净值落盘与组合风控**共用同一口径**（否则集中度与回撤点位互相矛盾）；
  4. 日志里不再出现 ``组合风控计算失败``（原缺陷的直接症状）。
"""
from __future__ import annotations

import logging

import pytest

# 市值 = 100×120(现价) + 1000×10(无现价→退回成本) = 12,000 + 10,000 = 22,000
# 净值 = 市值 22,000 + 可用现金 70,000 = 92,000
HOLDINGS = [
    {"code": "600519", "name": "贵州茅台", "quantity": 100,
     "cost_price": 100.0, "current_price": 120.0},
    {"code": "000001", "name": "平安银行", "quantity": 1000,
     "cost_price": 10.0},                       # 无 current_price
]
SNAP = {"total_capital": 100_000.0, "invested_cost": 30_000.0,
        "available_cash": 70_000.0, "max_position_pct": 0.30}
NET_WORTH = 92_000.0


def _make_sched(tmp_path, *, opportunity: bool = False):
    from quant_trading_system.stock_analysis.scheduler import MarketScheduler
    from quant_trading_system.utils import save_yaml

    cfg = {
        "enabled_markets": ["CN"],
        "stock_pools": {"CN": ["600519"]},
        "notify": {"email": {"enabled": False}},
        "schedule": {"poll_interval_sec": 60},
    }
    if opportunity:
        cfg["opportunity"] = {"enabled": True, "max_stocks": 5, "workers": 1}
    path = tmp_path / "notify.yaml"
    save_yaml(path, cfg)
    return MarketScheduler(str(path))


def _plans():
    """每次现造，避免 ``apply_brake_to_plans`` 原地改写污染别的用例。"""
    return [
        {"code": "600519", "name": "贵州茅台", "decision": "BUY_NOW",
         "entry_price": 100.0, "position_percent": 20.0, "stop_loss": 95.0},
        {"code": "000001", "name": "平安银行", "decision": "WATCH",
         "entry_price": 10.0, "position_percent": 20.0},
    ]


class _FakeScanResult:
    def __init__(self, plans):
        self.plans = plans
        self.gate_note = ""
        self.failed: list = []
        self.elapsed = 0.0


class _FakeScanner:
    def __init__(self, plans):
        self._plans = plans

    def scan(self, candidates, market=None):  # noqa: ARG002
        return _FakeScanResult(self._plans)


class _FakeNotifier:
    def __init__(self):
        self.sent: list = []

    def send(self, title, text, html=None):  # noqa: ARG002
        self.sent.append(title)


# --------------------------------------------------------------------------- #
# 共用口径：账户净值
# --------------------------------------------------------------------------- #
class TestNetWorth:
    def test_prefers_live_price_over_cost(self, tmp_path):
        sched = _make_sched(tmp_path)
        got = sched._net_worth(
            {"total_capital": 100_000.0, "available_cash": 0.0},
            [{"quantity": 100, "cost_price": 50.0, "current_price": 120.0}],
        )
        assert got == 12_000.0          # 用现价 120，不是成本 50

    def test_falls_back_to_cost_when_no_live_price(self, tmp_path):
        sched = _make_sched(tmp_path)
        got = sched._net_worth(
            {"total_capital": 100_000.0, "available_cash": 0.0},
            [{"quantity": 100, "cost_price": 50.0}],
        )
        assert got == 5_000.0

    def test_adds_available_cash(self, tmp_path):
        sched = _make_sched(tmp_path)
        assert sched._net_worth(SNAP, HOLDINGS) == NET_WORTH

    def test_missing_total_capital_returns_none(self, tmp_path):
        """拿不到总资金宁可缺一个点，也不能用 0 污染曲线与权重分母。"""
        sched = _make_sched(tmp_path)
        assert sched._net_worth({}, HOLDINGS) is None
        assert sched._net_worth(None, HOLDINGS) is None

    def test_zero_positions_and_cash_falls_back_to_total(self, tmp_path):
        sched = _make_sched(tmp_path)
        got = sched._net_worth(
            {"total_capital": 33_000.0, "available_cash": 0.0}, [])
        assert got == 33_000.0

    def test_bad_position_rows_do_not_crash(self, tmp_path):
        """持仓表脏数据只能拉低精度，不能让净值整体算不出来。"""
        sched = _make_sched(tmp_path)
        got = sched._net_worth(
            {"total_capital": 100_000.0, "available_cash": 1_000.0},
            [{"quantity": 10, "cost_price": 2.0, "current_price": None},
             {"quantity": 0, "cost_price": 5.0},
             {"quantity": 1}],                    # 无任何价格 → 计 0
        )
        assert got == 1_020.0


# --------------------------------------------------------------------------- #
# 净值落盘必须与组合风控同一把尺子
# --------------------------------------------------------------------------- #
class TestEquityRecordUsesNetWorth:
    def test_records_net_worth_not_total_capital(self, tmp_path, monkeypatch):
        from quant_trading_system.stock_analysis import portfolio_risk as PR

        recorded: list = []
        monkeypatch.setattr(PR, "record_equity", lambda v, **k: recorded.append(v))
        monkeypatch.setattr(PR, "load_equity_history", lambda **k: [])
        monkeypatch.setattr(PR, "equity_values", lambda h: [1.0])

        sched = _make_sched(tmp_path)
        sched._record_equity_daily(dict(SNAP), list(HOLDINGS))

        # 关键：写的是市值口径 92,000，而不是 total_capital 100,000。
        # 若两者分叉，回撤熔断会以一条与集中度不同口径的曲线判断是否 armed。
        assert recorded == [NET_WORTH]

    def test_no_total_capital_skips_point_but_returns_curve(self, tmp_path, monkeypatch):
        from quant_trading_system.stock_analysis import portfolio_risk as PR

        recorded: list = []
        monkeypatch.setattr(PR, "record_equity", lambda v, **k: recorded.append(v))
        monkeypatch.setattr(PR, "load_equity_history", lambda **k: [{"date": "2026-01-01"}])
        monkeypatch.setattr(PR, "equity_values", lambda h: [100_000.0])

        sched = _make_sched(tmp_path)
        curve = sched._record_equity_daily({}, list(HOLDINGS))

        assert recorded == []                       # 不写假点
        assert curve == [100_000.0]                 # 但已有曲线照样交出去


class TestShadowUsesSameNetWorth:
    def test_shadow_tick_uses_market_value_net_worth(self, tmp_path, monkeypatch):
        """影子模式的 equity 必须与组合风控同口径，否则「影子通过」不代表实盘通过。

        equity 在影子模式里既是仓位计算基准，又是单票/总仓位闸门的分母。分母用
        成本口径本金、分子用市值算持仓，会在有浮盈浮亏时给出互相矛盾的结论。
        """
        from quant_trading_system.stock_analysis import shadow_orders as SO

        monkeypatch.setattr(SO, "_RESULTS", tmp_path)
        captured: dict = {}
        real = SO.build_shadow_orders

        def spy(plans, **kw):
            captured.update(kw)
            return real(plans, **kw)

        monkeypatch.setattr(SO, "build_shadow_orders", spy)
        sched = _make_sched(tmp_path)
        sched._shadow_tick(_plans(), "CN", dict(SNAP), list(HOLDINGS))

        assert captured["equity"] == NET_WORTH          # 92,000，不是 100,000
        assert captured["holdings_value"] == 22_000.0


# --------------------------------------------------------------------------- #
# 接线：组合风控与回撤熔断到底有没有被执行
# --------------------------------------------------------------------------- #
@pytest.fixture
def harness(tmp_path, monkeypatch):
    """把 ``_run_market`` 的外部依赖全换成桩，只留风控接线是真实的。"""
    from quant_trading_system.stock_analysis import scheduler as S

    sched = _make_sched(tmp_path, opportunity=True)

    # ---- 持仓与资金 ----
    monkeypatch.setattr(sched.holdings, "compute_pnl",
                        lambda market=None: (list(HOLDINGS), {}))
    monkeypatch.setattr(sched.holdings, "capital_snapshot", lambda: dict(SNAP))
    monkeypatch.setattr(sched.holdings, "get_account",
                        lambda: {"total_capital": 100_000.0})

    # ---- 持仓动作 / 持仓量化：与本测试无关 ----
    monkeypatch.setattr(S, "analyze_holding_actions", lambda h: [])
    monkeypatch.setattr(sched, "_holdings_quant_for_market", lambda *a, **k: None)

    # ---- 机会扫描：喂一份计划进来，熔断要作用在它身上 ----
    monkeypatch.setattr(S, "OpportunityEngine", lambda **k: object())
    monkeypatch.setattr(S, "OpportunityBatchScanner",
                        lambda **k: _FakeScanner(_plans()))
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.market.regime_for_market",
        lambda *a, **k: (55.0, 1.0))
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.sector.fetch_sector_rank",
        lambda *a, **k: [])
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.sector.get_stock_sectors",
        lambda *a, **k: {})
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.screener.screen_candidates",
        lambda *a, **k: [{"code": "600519", "name": "贵州茅台"}])
    # 历史收益面板：本文件不测相关性与 VaR，直接当缺失
    monkeypatch.setattr(
        "quant_trading_system.stock_analysis.returns_panel.returns_for_holdings",
        lambda *a, **k: None)

    # ---- 邮件与旁路观测 ----
    monkeypatch.setattr(S, "build_market_message",
                        lambda *a, **k: ("标题", "正文", "<p>x</p>"))
    monkeypatch.setattr(sched, "notifier", _FakeNotifier())
    monkeypatch.setattr(sched, "_track_paper", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_shadow_tick", lambda *a, **k: None)
    monkeypatch.setattr(sched, "_record_equity_daily", lambda *a, **k: None)

    return sched


class TestPortfolioRiskWiring:
    def test_portfolio_risk_block_is_actually_called(self, harness, monkeypatch):
        """原缺陷：``total_equity`` NameError → 这个 spy 一次都进不来。"""
        from quant_trading_system.stock_analysis import holdings_quant as HQ

        calls: list = []
        monkeypatch.setattr(HQ, "portfolio_risk_block",
                            lambda h, **kw: (calls.append(kw) or
                                             {"verdict": "ok", "breaches": [], "brake": 1.0}))

        harness._run_market("CN")

        assert calls, "组合风控根本没被调用 —— 接线断了"
        assert calls[0]["total_equity"] == NET_WORTH

    def test_brake_reaches_trading_plans(self, harness, monkeypatch):
        """熔断系数必须真的落到计划的建议仓位上，而不只是打印一行日志。"""
        from quant_trading_system.stock_analysis import holdings_quant as HQ
        from quant_trading_system.stock_analysis import portfolio_risk as PR

        monkeypatch.setattr(HQ, "portfolio_risk_block",
                            lambda h, **kw: {"verdict": "回撤超限",
                                             "breaches": ["回撤 25% 超限"],
                                             "brake": 0.5})
        seen: dict = {}
        real = PR.apply_brake_to_plans

        def spy(plans, brake, **kw):
            seen["brake"] = brake
            seen["plans"] = plans
            return real(plans, brake, **kw)

        monkeypatch.setattr(PR, "apply_brake_to_plans", spy)
        harness._run_market("CN")

        assert seen.get("brake") == 0.5
        plans = seen["plans"]
        assert plans, "熔断没有拿到交易计划"
        # BUY_NOW 被按 50% 缩放；WATCH 不涉及新开仓，保持不变
        assert plans[0]["position_percent"] == 10.0
        assert plans[1]["position_percent"] == 20.0

    def test_no_brake_when_brake_is_one(self, harness, monkeypatch):
        """未触发熔断时不能去改计划（否则等于给所有信号无谓打折）。"""
        from quant_trading_system.stock_analysis import holdings_quant as HQ
        from quant_trading_system.stock_analysis import portfolio_risk as PR

        monkeypatch.setattr(HQ, "portfolio_risk_block",
                            lambda h, **kw: {"verdict": "ok", "breaches": [], "brake": 1.0})
        called: list = []
        monkeypatch.setattr(PR, "apply_brake_to_plans",
                            lambda *a, **k: called.append(a))

        harness._run_market("CN")
        assert called == []

    def test_no_silent_failure_in_log(self, harness, monkeypatch, caplog):
        """直接盯住原缺陷的症状：日志里不该再有『组合风控计算失败』。"""
        from quant_trading_system.stock_analysis import holdings_quant as HQ

        monkeypatch.setattr(HQ, "portfolio_risk_block",
                            lambda h, **kw: {"verdict": "ok", "breaches": [], "brake": 1.0})

        with caplog.at_level(logging.WARNING):
            harness._run_market("CN")

        assert "组合风控计算失败" not in caplog.text

    def test_bad_holdings_do_not_break_the_push(self, harness, monkeypatch):
        """组合风控是附加信息：它自己炸了，邮件也必须照发。"""
        from quant_trading_system.stock_analysis import holdings_quant as HQ

        def boom(h, **kw):
            raise RuntimeError("风控炸了")

        monkeypatch.setattr(HQ, "portfolio_risk_block", boom)
        harness._run_market("CN")

        assert harness.notifier.sent, "风控失败不应阻断推送"
