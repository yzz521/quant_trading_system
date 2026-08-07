# quant_trading_system

事件驱动的多市场量化交易框架，并附带 **A 股个人持仓助手**（卖出区间、诊断、定时推送）。

- **License**: MIT  
- **Python**: ≥ 3.10  
- **仓库**: https://github.com/yzz521/quant_trading_system  

---

## 产品双轨

| 轨道 | 做什么 | 怎么进 |
|------|--------|--------|
| **A. 量化框架** | 数据 → 策略 → 风控 → 回测 / Paper | `examples/backtest_demo.py`、`BacktestEngine` |
| **B. A 股助手** | 持仓 SQLite、卖出区间、诊断扫描、邮件推送 | `python deploy/ctl.py start-all` → http://localhost:8502 |

助手用于研究与决策辅助，**不会自动下真实订单**。CTP / IBKR / Binance 等实盘适配器默认仍是骨架（`NotImplementedError`），需自行接 SDK，见 [`docs/LIVE_BROKERS.md`](docs/LIVE_BROKERS.md)。

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/yzz521/quant_trading_system.git
cd quant_trading_system

python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -e ".[dev,data,dashboard]"
# 或: pip install -r requirements.txt && pip install streamlit akshare
```

包目录名需为 `quant_trading_system`，以便 `import quant_trading_system`。

### 2. 一键启动（推荐）

跨平台统一入口是 **`deploy/ctl.py`**（不依赖 bash）：

```bash
# 任意系统（在项目根目录）
python deploy/ctl.py start-all
```

| 系统 | 命令 |
|------|------|
| **任意平台** | `python deploy/ctl.py start-all` |
| Windows CMD | `deploy\ctl.bat start-all` |
| Windows PowerShell | `.\deploy\ctl.ps1 start-all` |
| macOS / Linux | `./deploy/ctl.sh start-all`（内部转调 ctl.py） |

浏览器只打开 **一个地址**：

| 地址 | 说明 |
|------|------|
| **http://localhost:8502** | 左侧菜单：持仓与卖出区间 / 个股诊断与扫描 / 研究工具（回测与风险） |

```bash
python deploy/ctl.py status
python deploy/ctl.py stop-all
python deploy/ctl.py restart-all

# 看板 + 定时推送（需先配置 config/notify.yaml）
python deploy/ctl.py start-all --with-scheduler
```

单独控制：

```bash
python deploy/ctl.py dashboard start|stop|status|log
python deploy/ctl.py scheduler start|stop|status|log
```

端口用环境变量 `PORT` 覆盖（默认 **8502**）。

### 3. 运行测试与示例

```bash
# 若未 editable 安装，需能 import 到包
export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"   # Linux/macOS
# Windows CMD: set PYTHONPATH=%CD%\..

pytest -q

python examples/backtest_demo.py
python examples/paper_loop_demo.py
python examples/universe_backtest_demo.py
```

---

## 架构概览

```
DataFeed / DataSource
    → MarketEvent → Strategy → SignalEvent
    → ExecutionHandler + RiskManager → OrderEvent
    → Broker (Simulated / Paper) → FillEvent → Portfolio
```

- 回测默认 **下一根 open 成交**（`fill_policy=next_open`），避免偷看当根收盘价。  
- A 股约束可配：T+1、涨跌停、成交量上限、`lot_size=100`、卖出印花税。  
- Paper / Live：`EventEngine(thread_safe=True)`，支持定时拉行情（`LiveBarPoller`）。

更细说明见 [`docs/architecture.md`](docs/architecture.md)。

---

## 目录结构

```text
quant_trading_system/
├── core/              # 事件、EventEngine
├── data/              # AkShare / yfinance / 合成 / Fallback / BarFeed
├── strategy/          # 策略 + create_strategy 注册表
├── risk/              # 仓位 / 回撤 / T+1 / 投影权重 / 下单频率
├── portfolio/         # 现金、持仓、权益曲线
├── backtest/          # SimulatedBroker、Optimizer（网格 / Walk-Forward）
├── execution/         # PaperBroker、LiveEngine、LiveBarPoller、券商骨架
├── analytics/         # 绩效指标、基准对比、HTML 报告
├── stock_analysis/    # 持仓 DB、卖出区间、诊断、扫描、调度推送
├── dashboard/         # Streamlit 多页：首页 + 持仓 / 诊断 / 研究工具
├── deploy/            # ctl.py / ctl.sh / ctl.ps1 / ctl.bat
├── examples/
├── config/            # settings、*.yaml.example（勿提交真实密钥）
├── tests/
└── docs/
```

---

## 常用能力速查

### 回测

```python
from quant_trading_system.backtest import BacktestConfig, BacktestEngine
from quant_trading_system.data import BarFeed, SyntheticDataSource
from quant_trading_system.strategy import create_strategy

cfg = BacktestConfig(t1_enabled=True, enforce_limit=True, lot_size=100)
feed = BarFeed({"600000": df}, calendar_market="CN")
eng = BacktestEngine(cfg)
eng.add_strategy(create_strategy("ma_cross", symbols=["600000"], fast=5, slow=20))
portfolio = eng.run(feed)
```

### 参数网格 / Walk-Forward

```python
from quant_trading_system.backtest import grid_search, walk_forward, walk_forward_summary
```

### 基准对比

```python
from quant_trading_system.analytics import compute_benchmark_metrics
m = compute_benchmark_metrics(portfolio, hs300_close_series)
# information_ratio, excess_total_return, beta, ...
```

### 数据多源降级

```python
from quant_trading_system.data import FallbackDataSource, AkShareSource, LocalParquetSource
src = FallbackDataSource([AkShareSource(), LocalParquetSource("data_cache")])
```

### 持仓 → 风险诊断 / 回测股票池

```python
from quant_trading_system.stock_analysis.risk_diagnosis import diagnose_holdings
from quant_trading_system.stock_analysis.universe import make_universe
```

### 卖出区间（含深套分批路径）

浏览器 **http://localhost:8502** → 左侧 **「持仓与卖出区间」**；或：

```python
from quant_trading_system.stock_analysis.sell_zone import analyze_sell_zone
analyze_sell_zone({"code": "002269", "cost_price": 3.595})
```

### Paper 闭环

```bash
python examples/paper_loop_demo.py
python examples/paper_poll_demo.py --seconds 5
```

---

## 定时推送（调度器）

看板与调度器共用 **`config/holdings.db`**（由 `Holdings` 从 `config/holdings.yaml` 路径解析，与路径 A 一致）。

1. 复制并编辑推送配置（**不要**提交真实密钥）：

```bash
cp config/notify.yaml.example config/notify.yaml
# 打开 notify.email.enabled，填写 SMTP 授权码与收件人
```

2. 测试一发（可指定市场，不依赖是否开盘）：

```bash
python examples/run_scheduler.py --test --market CN
```

3. 常驻：

```bash
python deploy/ctl.py scheduler start
# 或
python deploy/ctl.py start-all --with-scheduler
```

推送内容包括：持仓盈亏、自选诊断、可选扫描命中；若已合入持仓动作模块，还会包含 **卖出区间 / 止损 / 深套分批 / 加仓参考**（研究辅助，非投资建议）。

日志：`results/scheduler.log`。说明见 [`docs/SCHEDULER_HOLDINGS.md`](docs/SCHEDULER_HOLDINGS.md)。

---

## 与 Vibe-Trading 二次分析联动

本系统可以把 **持仓 + 扫描命中候选股** 投喂给本地 [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
（HKU 开源的个人交易 Agent）做二次分析，结果回填到邮件与看板。

```bash
# 终端1：启动 Vibe（首次需 pip install vibe-trading-ai 并 vibe-trading init）
vibe-trading serve --port 8899

# 终端2：看板「Vibe 二次分析」页手动发起；或配置后随邮件自动执行
python deploy/ctl.py start-all
```

- 载荷内容：`holdings`（持仓）+ `candidates`（扫描命中 Top 15，调度器每小时落盘
  `results/latest_scan.json`，页面/CLI/邮件统一读取）。
- 配置：`config/notify.yaml` 的 `vibe:` 段（`enabled` / `on_email` / `candidate_count`）。
- 详细说明见 [docs/VIBE_BRIDGE.md](docs/VIBE_BRIDGE.md)、[docs/VIBE_ON_EMAIL.md](docs/VIBE_ON_EMAIL.md)。

---

## 一键发布便携包（Release）

打一个 `v*` tag 即可由 GitHub Actions 自动构建 **macOS / Windows** 便携包并挂到
Release（内含独立 Python 运行时 + 全部依赖 + 一键启动脚本，解压双击即用）：

```bash
git tag v0.1.0
git push origin v0.1.0
```

构建配置见 `.github/workflows/release.yml`，启动脚本与依赖清单在 `packaging/`。
便携包默认关闭登录门禁、持仓为空；数据与配置均在本机，不入包。

---

## 配置与安全

| 文件 | 说明 |
|------|------|
| `config/settings.yaml` | 回测/费用等默认参数 |
| `config/holdings.yaml.example` | 旧版 YAML 示例；现持仓在 **`config/holdings.db`** |
| `config/notify.yaml.example` | 复制为 `notify.yaml` 后填推送凭证 |

**不要**把 `notify.yaml`、真实 `holdings.db`、SMTP / Server酱 / 飞书密钥提交到 Git。

---

## 实盘适配器

| 文件 | 说明 |
|------|------|
| `execution/paper_broker.py` | 模拟成交，开箱可用 |
| `execution/binance_broker.py` | 币安骨架 → 接 `python-binance`（建议先 Testnet） |
| `execution/ibkr_broker.py` | 盈透骨架 → 接 `ib_insync` + TWS/Gateway Paper |
| `execution/ctp_broker.py` | CTP 期货骨架 → 接 `vnpy_ctp` / 柜台 API + SimNow |

SDK **不随本仓库分发**，需自行安装并在 `# TODO` 处接线。步骤见 [`docs/LIVE_BROKERS.md`](docs/LIVE_BROKERS.md)。

---

## 开发

```bash
pip install -e ".[dev]"
pytest -q
ruff check .    # 若已安装 ruff
```

贡献约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 平台说明

| 能力 | Windows | macOS | Linux |
|------|---------|-------|-------|
| `python deploy/ctl.py` | ✅ | ✅ | ✅ |
| `deploy/ctl.bat` | ✅ | — | — |
| `deploy/ctl.ps1` | ✅ | — | — |
| `deploy/ctl.sh` | 需 WSL/Git Bash | ✅ | ✅ |
| Streamlit 看板 | ✅ | ✅ | ✅ |
| `trade_monitor` 本机通知 | 视实现 | 偏 macOS | 视实现 |

**不依赖 shell 脚本**：全程使用 `python deploy/ctl.py ...` 即可管理服务。

---

## 路线与状态（摘要）

已落地：工程基建（MIT / pyproject / tests）、A 股 T+1 / 涨跌停 / 量能、组合权重与下单频率、网格与 Walk-Forward、基准对比、Paper 线程安全与定时拉行情、多源降级、卖出区间深套路径、统一看板（单端口）、跨平台 ctl、定时分析推送（持仓与看板同库）。

实盘券商通道仍为骨架；接入前请先用 Paper 全链路验证。

---

## 致谢

数据层可插拔设计便于替换为 Tushare / 券商行情等；欢迎 Issue / PR。
