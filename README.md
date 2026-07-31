# 量化交易系统 (Quantitative Trading System)

一套**事件驱动**、**多市场**、**可扩展**的 Python 量化交易框架，覆盖从数据接入、策略研发、回测验证到模拟盘 / 实盘交易的完整链路。

- **多市场**：A股 / 港股 / 美股 / 期货 / 加密货币 / 黄金（通过可插拔数据源）
- **多策略**：趋势跟踪、均值回归、多因子选股、机器学习（内置 4 类示例策略）
- **统一架构**：回测与实盘共用同一事件引擎，策略代码零改动切换
- **完整风控**：仓位 / 暴露 / 回撤 / 现金熔断，订单必经风控网关
- **可观测**：HTML 绩效报告 + Streamlit 监控看板

---

## 一、架构总览

系统采用经典**事件驱动**架构，所有组件通过事件队列解耦：

```
┌──────────┐  MarketEvent   ┌──────────┐  SignalEvent  ┌──────────────┐
│ DataFeed │ ─────────────▶ │ Strategy │ ────────────▶ │ Execution    │
│  数据层   │                │  策略层   │                │ Handler      │
└──────────┘                └──────────┘                │ (风控+仓位)   │
      ▲                          ▲                      └──────┬───────┘
      │                          │ FillEvent                   │ OrderEvent
      │                          │                            ▼
┌──────────┐  FillEvent     ┌──────────┐               ┌──────────────┐
│ Portfolio│ ◀──────────── │  Broker  │ ◀────────────  │ RiskManager  │
│  组合层   │                │ 经纪商   │                │   风控层      │
└──────────┘                └──────────┘               └──────────────┘
```

**事件流**：数据 → 策略产生信号 → 风控+仓位计算 → 下单 → 经纪商成交回报 → 更新组合 → 通知策略。

### 目录结构

```
quant_trading_system/
├── core/              # 事件引擎与数据结构（系统骨架）
│   ├── event.py       # Event/Bar/Signal/Order/Fill 定义
│   └── engine.py      # EventEngine 主循环
├── data/              # 数据层
│   ├── data_source.py # DataSource 抽象基类
│   ├── akshare_source.py   # A股/港股/期货 (AkShare)
│   ├── yfinance_source.py  # 美股/黄金 (yfinance)
│   ├── synthetic_source.py # 合成数据（离线测试）
│   ├── cache.py       # 磁盘缓存
│   └── feed.py        # BarFeed 回测数据馈送
├── strategy/          # 策略层
│   ├── base.py        # Strategy 基类
│   ├── trend_following.py   # 双均线 / 海龟突破
│   ├── mean_reversion.py    # 布林带
│   ├── multi_factor.py      # 多因子选股
│   └── ml_strategy.py       # 机器学习 (随机森林)
├── backtest/          # 回测层
│   ├── broker.py      # SimulatedBroker 模拟撮合
│   ├── execution_handler.py # 信号→订单（含风控）
│   └── engine.py      # BacktestEngine 编排
├── execution/         # 实盘执行层
│   ├── broker_base.py # LiveBroker 抽象
│   ├── paper_broker.py# 模拟盘（开箱即用）
│   ├── ctp_broker.py  # 期货 CTP 骨架
│   ├── ibkr_broker.py # 美股 IBKR 骨架
│   ├── binance_broker.py # 加密货币 Binance 骨架
│   └── live_engine.py # LiveTradingEngine
├── risk/              # 风控层（仓位/暴露/回撤/现金）
├── portfolio/         # 组合管理 + 仓位分配
├── analytics/         # 绩效指标 + 可视化 + HTML 报告
├── ml/                # ML 研究工具箱（特征/模型/walk-forward）
├── dashboard/         # Streamlit 监控看板
├── examples/          # 可运行示例
├── config/            # 配置文件
└── results/           # 回测报告输出
```

---

## 二、安装

```bash
# 推荐使用 Python 3.10+
pip install -r quant_trading_system/requirements.txt
```

依赖分三类（按需安装）：
- **核心**：pandas, numpy, matplotlib, pyarrow, pyyaml
- **数据源**：akshare（A股/港股/期货）, yfinance（美股/黄金）
- **ML / 看板**：scikit-learn, streamlit
- **实盘**（可选）：python-binance, vnpy_ctp, ib_insync

---

## 三、快速开始（30 秒跑通）

```bash
cd quant_trading_system
python examples/backtest_demo.py
```

该示例使用**合成数据**（无需网络），跑双均线策略并生成 HTML 报告。打开 `results/backtest_demo.html` 查看净值曲线、回撤、月度收益与持仓。

### 最小代码示例

```python
from quant_trading_system import BacktestEngine, BacktestConfig, PerformanceReport
from quant_trading_system.data import SyntheticDataSource, BarFeed
from quant_trading_system.strategy import MovingAverageCrossStrategy

# 1. 准备数据
ds = SyntheticDataSource(seed=42)
df = ds.get_history("DEMO", "2022-01-01", "2024-12-31")
feed = BarFeed({"DEMO": df})

# 2. 配置引擎 + 策略
engine = BacktestEngine(BacktestConfig(initial_capital=1_000_000))
engine.add_strategy(MovingAverageCrossStrategy(["DEMO"], fast=5, slow=20))

# 3. 运行回测
portfolio = engine.run(feed)

# 4. 生成报告
PerformanceReport(portfolio).to_html("results/report.html")
```

---

## 四、使用真实数据

把 `SyntheticDataSource` 换成对应市场的数据源即可：

```python
from quant_trading_system.data import AkShareSource, YFinanceSource, AssetClass, DiskCache

# A股（前复权日线）
ds = AkShareSource(AssetClass.EQUITY_CN)
df = ds.get_history("600000", "2022-01-01", "2024-12-31", adjust="qfq")

# 美股
ds = YFinanceSource(AssetClass.EQUITY_US)
df = ds.get_history("AAPL", "2022-01-01", "2024-12-31")

# 黄金（期货代码 GC=F）
ds = YFinanceSource(AssetClass.COMMODITY)
df = ds.get_history("GC=F", "2022-01-01", "2024-12-31")

# 配合磁盘缓存，避免重复下载
cache = DiskCache("results/data_cache")
df = ds.get_history_cached("600000", "2022-01-01", "2024-12-31", cache=cache)
```

---

## 五、策略开发指南

继承 `Strategy`，实现 `on_bar` 即可。策略只负责**发信号**，仓位与风控由系统处理：

```python
from quant_trading_system.strategy import Strategy
from quant_trading_system.core import Bar, Direction

class MyStrategy(Strategy):
    def __init__(self, symbols, threshold=0.02, **kw):
        super().__init__(symbols, name="MyStrategy", threshold=threshold, **kw)
        self.threshold = threshold

    def on_bar(self, bar: Bar) -> None:
        closes = self.to_series(bar.symbol, "close")
        if len(closes) < 10:
            return
        momentum = closes.iloc[-1] / closes.iloc[-10] - 1
        pos = self.position(bar.symbol)

        if momentum > self.threshold and pos <= 0:
            self.emit_signal(bar.symbol, Direction.LONG, strength=1.0)
        elif momentum < -self.threshold and pos > 0:
            self.emit_signal(bar.symbol, Direction.EXIT)
```

关键 API：
- `self.to_series(symbol, field, n)` — 取滚动行情序列
- `self.position(symbol)` — 当前持仓
- `self.emit_signal(symbol, direction, strength)` — 发交易信号
- `self.close_all()` — 一键平仓
- `on_fill(fill)` — 成交回调

---

## 六、风控配置

风控在 `BacktestConfig` / `RiskManager` 中集中配置：

| 参数 | 说明 | 默认 |
|------|------|------|
| `max_positions` | 最大持仓标的数 | 10 |
| `max_position_pct` | 单标的最大仓位占比 | 25% |
| `max_exposure` | 总暴露度 / 权益 | 1.0 |
| `max_drawdown` | 触发回撤熔断（停止开新仓） | 20% |
| `min_cash_ratio` | 最低现金保留 | 5% |
| `position_weight` | 单笔仓位权重（EqualWeightSizer） | 10% |

切换仓位算法：`engine.use_sizer(VolTargetSizer(target_vol=0.15))`。

---

## 七、实盘对接

### 模拟盘（开箱即用）

```python
from quant_trading_system.execution import LiveTradingEngine, LiveConfig, PaperBroker
from quant_trading_system.strategy import MovingAverageCrossStrategy

broker = PaperBroker(initial_cash=1_000_000)
engine = LiveTradingEngine(broker, LiveConfig(lot_size=1))
engine.add_strategy(MovingAverageCrossStrategy(["600000"], fast=5, slow=20))
engine.run()   # 阻塞运行，接入实时数据后即生效
```

### 真实账户

实盘 broker 是**骨架**，需按文件内 `# TODO` 注释对接厂商 SDK 并填入凭证：

| 市场 | Broker | 依赖 | 凭证 |
|------|--------|------|------|
| 期货 | `CTPBroker` | vnpy_ctp | 投资者ID/密码/前置地址 |
| 美股 | `IBKRBroker` | ib_insync | TWS/Gateway 端口 |
| 加密货币 | `BinanceBroker` | python-binance | API Key/Secret |

> ⚠️ **风险提示**：实盘交易涉及真实资金，务必先在模拟环境（Simnow / IBKR Paper / Binance Testnet）充分验证。骨架文件默认抛 `NotImplementedError`，防止误下单。

---

## 八、Streamlit 监控看板

```bash
streamlit run quant_trading_system/dashboard/app.py
```

支持在网页上选择数据源、标的、策略与参数，一键回测并查看净值曲线、回撤、月度收益、持仓与绩效指标。

---

## 九、绩效指标

`PerformanceReport` / `compute_metrics` 输出：

- 收益：总收益、年化收益、最终权益
- 风险：年化波动、最大回撤、回撤持续期、Calmar
- 风险调整：Sharpe、Sortino
- 交易：成交笔数、胜率、盈亏比（Profit Factor）、单笔盈亏、期望收益

图表遵循**中国习惯**：红涨绿跌。

---

## 十、设计要点

1. **无前视偏差**：订单在产生后的**下一根 bar 的开盘价**成交（`fill_policy=next_open`）。
2. **回测=实盘**：同一 `EventEngine` 驱动两者，策略不感知运行环境。
3. **可插拔**：数据源、Broker、Sizer、风控均可替换，无需改动策略代码。
4. **成本真实**：佣金、印花税、滑点、最小手续费均可配置。
5. **离线可用**：合成数据源保证无网络环境下也能开发与测试。

---

## 许可与免责

本项目仅供学习研究。量化交易有风险，实盘需谨慎，使用者自负盈亏。
