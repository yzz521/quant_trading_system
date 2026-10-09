# quant_trading_system · GP助手

[English](README_EN.md) | 中文

[![CI](https://github.com/yzz521/quant_trading_system/actions/workflows/ci.yml/badge.svg?branch=main-v3)](https://github.com/yzz521/quant_trading_system/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yzz521/quant_trading_system)](https://github.com/yzz521/quant_trading_system/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)](https://github.com/yzz521/quant_trading_system/releases/latest)

**main-v3** 是 A 股 / 港股 / 美股的每日投研助手：全市场初筛 → 板块轮动 → 9 因子评分 → 交易计划 → 防未来函数回测 → AI 解读。交付方式是本地看板、每日邮件，以及可选的桌面安装包。

> **量化计算 · AI 解释 · 回测验证 · 你做决策。**
> 价格、止损、目标和仓位由量化算出来。AI 只解释这些已经算过的数，不能另写一个价。回测用来检验规则。买不买由你决定。本分支不向券商发送委托。

> **不构成投资建议。** 仅供研究与学习。评分、区间、仓位和 BUY/SELL 标签都是模型输出，不构成任何投资建议。据此操作，风险自负。

历史分支：`main-v2`（更完整的 V2）、`main`（V1 事件驱动框架，本分支已不含那套模块）。仓库：<https://github.com/yzz521/quant_trading_system>。当前应用版本在 `utils/app_meta.py` 的 `APP_VERSION`，与 [最新 Release](https://github.com/yzz521/quant_trading_system/releases/latest) 对齐。变更记录看 [Releases](https://github.com/yzz521/quant_trading_system/releases)。

**[下载桌面版](https://github.com/yzz521/quant_trading_system/releases/latest)**（macOS / Windows / Linux，无需先装 Python）。

---

## 界面预览

下面是占位图，还不是真实界面。替换时请用 `python examples/run_opportunity.py --synthetic`，或把股票名称打码，不要提交真实持仓。

| 今日机会 | 交易计划与 AI 解读 | 持仓指挥台 |
| --- | --- | --- |
| ![今日机会占位](docs/images/opportunity.svg) | ![交易计划占位](docs/images/plan-ai.svg) | ![持仓占位](docs/images/holdings.svg) |

| 因子验证 | 每日邮件 |
| --- | --- |
| ![因子验证占位](docs/images/factors.svg) | ![邮件占位](docs/images/email.svg) |

邮件样例可由 `python examples/gen_email_preview.py` 写到 `results/email_cn_preview.html`（以及 `us` / `hk`）。演示动图尚未放入仓库；若要补，建议 15–30 秒，用合成数据走一遍：打开桌面版 → 填写预计投入 → 扫描 → 打开一条计划的 AI 解读 → 看回测结果。

---

## 功能一览

| 功能 | 说明 | 入口 |
|------|------|------|
| 🎯 **今日计划** | 全市场初筛（A 股 / 港股 / 美股）→ 板块轮动 → 9 因子评分 → 交易计划（入场区间 / 止损 / 三档目标 / RR / 仓位）→ 历史回测 → AI 解读。仓位按真实指数算出的市场状态调节。**未设置预计投入金额时不扫描。** | 看板「今日机会」、`examples/run_opportunity.py` |
| 💼 **我的持仓** | SQLite 持仓（增删改、加权成本）、盈亏、粘贴成交同步、持仓量化（卖出 / 减仓 / 持有 / 可加仓） | 看板「持仓指挥台」、`examples/my_holdings.py` |
| 🎯 **卖出/加仓参考** | 卖出区间、止损、深套分批路径、加仓参考 | 持仓页、每日邮件 |
| 📧 **每日邮件** | 有数据才出现对应区块：持仓、资金账户、组合风控、持仓量化（每个交易日一次）、今日机会、卖出/加仓参考。交易日按调度推送。 | 看板「配置」、`examples/run_scheduler.py` |
| 📊 **因子验证** | IC/IR、分位单调性、滚动前瞻。结论会改**组合分**权重；报告缺失或过期则回到基线权重。 | `dashboard/pages/3_factors.py`、`examples/validate_factors.py` |
| 👁 **实时盯盘** | 盘中轮询持仓/自选，命中规则才推送。**默认关闭。** | 配置页「实时盯盘」、`examples/run_realtime.py` |
| ⚙️ **配置** | 邮件开关、收件地址、监测市场（CN / HK / US）、扫描参数、可选的同花顺 Key。打包版可检查并安装 GitHub 新版本。 | 看板「配置」 |

决策状态：🟢 `BUY_NOW` / 🟢 `BUY_ON_PULLBACK` / 🟡 `WATCH` / 🟠 `HOLD` / 🔴 `SELL` / ⛔ `AVOID`。RR &lt; 1.5、几何不自洽，或质量闸门硬否决时为 `AVOID`，此时不计算仓位。

---

## 筛选流水线

```
全市场快照
   ↓ ① 硬过滤：成交额下限 + 涨跌幅区间 + 名称剔除 + 流通市值
        同一行业默认最多占候选池 25%，不足再补齐
Top N（默认 30，看板可调 5–80）
   ↓ ② 板块轮动：新浪行业强度（涨跌幅百分位 60% + 成交额百分位 40%）
   ↓ ③ 9 因子个股分（权重和 = 1.00）
        fundamental .12  growth .08  technical .20  momentum .05
        capital_flow .15  valuation .10  market_env .05  sector .05  risk .20
   ↓ ④ 机会分（0–100）+ 组合分排序
        机会分：价位 20% · 支撑 15% · 趋势 15% · 距入场 15% · RR 20% · 波动 5% · 相似形态 10%
        组合分默认：质量 45% · 时机 35% · 赔率 20%（可被因子验证降权）
   ↓ ⑤ 机会引擎：支撑阻力 → 入场 → 止损/目标 → RR → 仓位 → 决策
🟢 买入列表（BUY_NOW / BUY_ON_PULLBACK）  🟡 关注列表（WATCH）
```

- **初筛**（`screener.py`）：拉一次快照，不在这一步拉 K 线。A 股默认成交额 ≥ 5000 万、流通市值下限 20（`float_cap_yi`，即 20 亿元）；港股默认成交额 ≥ 1000 万港元（初筛注释如此），流通市值下限 5，单位与快照字段相同。涨跌幅默认 −6%～10%。名称含 `ST` / `退` / `*` 会剔除。`N` / `C` 只在名称含中文、且出现在开头时剔除（新股标记），避免误伤 `TCL科技` 或纯英文名。美股走 Nasdaq screener：现价 &gt; 2 美元、市值 ≥ 100 亿美元，并去掉名称里带 ETF / ETN / Fund / Trust 的条目；失败则回退配置池加一份知名股票列表。快照里的股票只数会随市场变化，README 不写死。
- **板块**（`sector.py`）：强度与成分都来自新浪行业接口（代码注释写的是 49 个行业）。成分映射缓存 24 小时。未命中或失败时板块因子为中性 50，不挡住主流程。
- **9 因子**（`scoring/stock_score.py`）：技术趋势看均线结构，MACD / ADX 确认方向和强度。RSI / KDJ / CCI / WR 只做超买风险过滤，不单独成因子。K 线形态进入机会分的「相似形态」（10%）。斐波那契回撤参与支撑/阻力与入场锚点。近 14 日公告和新闻关键词并入 **risk（20%）**：减持、立案等降分，回购、中标等小幅加分。缺财务数据的维度会从加权里拿掉，而不是当成「正常的 50 分」去稀释其他因子。
- **排序**：批量扫描默认按组合分，不是只按机会分（`opportunity/batch_scanner.py`，`sort_key="composite"`）。
- **质量闸门**（`opportunity/quality_gate.py`）：缺失不等于通过。每条没过的规则都会带原因。回测拿不到历史财报时，可以放宽「数据完整性」，分数和 RR 门槛仍在。

---

## 设计上怎么卡住胡说

1. **大模型不定价。** `stock_analysis/ai/guard.py` 在展示前检查三段：文本里的数字必须能对上计划里的数（编造价格优先拦截）；不得与决策相反（`WATCH` / `AVOID` 不能写成买入，`SELL` 不能写成买入或持有）；出现买卖用语时必须带固定免责声明。任一失败就改显示规则解读。没配 AI 时本来就用规则解读。
2. **回测不看未来。** `backtest/trading_plan_backtest.py` 只用截至 T 日的 K 线生成计划，只用 T 日之后的 K 线评估结果。指标是因果计算的。引擎默认 `fetch_news=False`，所以回测不会把今天的公告套到历史 K 线。
3. **成交按能成交的价。** `backtest/execution.py`：限价买必须真的触及委托价；扣除佣金、印花税（卖出）、过户费和滑点。跳空穿过止损时用开盘价（更差的一边），跳空穿过目标时同样用开盘价。一字涨跌停和停牌日不成交。单日成交额有参与率上限。
4. **验证结果会改权重，但只改组合分。** `research/factor_validation.py` 算 IC/IR、分位单调性和滚动前瞻。`scoring/factor_weights.py` 把 `stock_score` / `opportunity_score` / `risk_reward_1` 的结论变成组合分三个维度的乘数。样本不足不惩罚；报告缺失、损坏或超过 `max_age_days`（默认 30 天）就回到基线（质量 45% / 时机 35% / 赔率 20%）。它**不会**改上面那组 9 因子权重。
5. **置信度可以标成概率。** `calibration.py` 用 Platt（样本较少时）或 isotonic（样本达到门槛时）做单调映射。给了时间顺序就按时间分块做交叉验证，避免相邻样本泄漏。样本不够就保持原打分。默认不加载标定器，排序和决策不变。
6. **影子模式先于任何实盘。** `shadow_orders.py` 把 `BUY_NOW` / `BUY_ON_PULLBACK` 写成订单意图，过五道闸门（单日亏损、单票上限、总仓位、断线即停、同一日同一代码同一方向不重复），追加写入 `results/shadow_orders.jsonl`。模块里没有下单函数，也不导入券商接口。

---

## 快速开始

### 0. 桌面应用（推荐）

推送 `v*` 标签会同时触发两条 GitHub Actions（都上传到 [Releases](https://github.com/yzz521/quant_trading_system/releases)）：

| 工作流 | 产物 | 是什么 |
|--------|------|--------|
| `build-app` | `GP-Assistant-macOS-arm64.zip`、`GP-Assistant-macOS-x64.zip`、`GP-Assistant-Windows.zip`、`GP-Assistant-Linux.tar.gz` | PyInstaller 桌面应用 |
| `release-portable` | `quant_trading_system-portable-windows-x64.zip`、`…-macos-arm64.zip`、`…-macos-x64.zip`、`…-linux-x64.zip` | 带 Python 运行时的便携源码包 |

标签请用还没占用的版本号。`v0.3.12` 已经发过，不要再当示例去打。

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

应用版本号改 `utils/app_meta.py` 的 `APP_VERSION`，与标签一致（`0.3.12` → 标签 `v0.3.12`）。

下载桌面包之后：

- **Windows**：解压整个 `GPAssistant` 文件夹，运行 `GPAssistant.exe`。SmartScreen 出现时选「更多信息」→「仍要运行」。不要只拷贝 exe。
- **macOS**（无公证）：解压后双击 `首次打开.command`，或执行  
  `xattr -cr GP助手.app && codesign --force --deep --sign - GP助手.app && open GP助手.app`
- **Linux**：包内是 `GP助手` 可执行文件，系统需要 `python3-gi` 和 `gir1.2-webkit2-4.1`。

本地打包（调试用）：

```bash
pip install -e ".[data,dashboard,gui]" pyinstaller
pyinstaller app/packaging/gp_assistant.spec --noconfirm
```

详见 [`app/README.md`](app/README.md)。

仓库里还有一个 git 标签 `v1.0.0`。它是旧 `main`（V1）留下的，**没有对应的 GitHub Release**。数字比 `v0.3.12` 大，但不是当前这条发布线的最新版。应用内「检查更新」读的是 Releases API（`utils/app_meta.py`），不是仓库里最大的 tag。

### 1. 安装（从源码跑看板）

```bash
git clone https://github.com/yzz521/quant_trading_system.git
cd quant_trading_system
git checkout main-v3

python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1

pip install -e ".[all]"            # data + dashboard + gui + dev
```

只要看板和测试、不要桌面壳时，与 CI 相同：`pip install -e ".[dev,data,dashboard]"`。

### 2. 启动

```bash
# 无参数：重启调度器 + 看板 + 实时盯盘
./deploy/restart.sh
./deploy/restart.sh status

# 任意平台
python deploy/ctl.py dashboard start
python deploy/ctl.py scheduler start
```

看板：<http://localhost:8502>（首页 + 今日机会 + 持仓指挥台 + 配置 + 因子验证）。  
调度器在 macOS 上优先走 launchd（`com.gp.stock-scheduler`，需已 bootstrap）；否则 `restart.sh` 回退到 `ctl.py`。

### 3. 测试与示例

```bash
pytest -q
ruff check stock_analysis dashboard utils examples

python examples/run_opportunity.py 600000 --account 100000   # 联网
python examples/run_opportunity.py --synthetic               # 离线合成数据
python examples/run_batch_opportunity.py 600000 000001 600519
python examples/run_backtest_plan.py 600000 --days 750
python examples/gen_email_preview.py
python examples/check_notify.py
```

`examples/` 里目前有 17 个脚本，不全是冒烟演示。其余包括：`validate_factors.py`、`fit_confidence_calibration.py`、`calibrate_weights.py`、`run_shadow.py`、`run_realtime.py`、`check_data_sources.py`、`my_holdings.py`、`run_scheduler.py`，以及 `audit_*.py` / `calc_exposure_reduction.py` 这几份审计脚本。

---

## 每日邮件

```bash
cp config/notify.yaml.example config/notify.yaml   # 不要提交真实密钥
python examples/run_scheduler.py --test --market CN
python deploy/ctl.py scheduler start
```

也可以在看板「配置」页改开关、收件人和监测市场。渠道：邮件（SMTP）、Server酱、飞书。日志：`results/scheduler.log`。

`notifier.build_market_message` 的区块顺序是：持仓 → 资金账户 → 组合风控 → 持仓量化 → 今日机会 → 卖出/加仓参考，外加免责声明。某一项没有数据时，那一块不会出现。`examples/gen_email_preview.py` 的样例数据只填了持仓、持仓量化、今日机会和卖出参考。

---

## 目录结构

```text
stock_analysis/
├── opportunity/     支撑阻力、入场、止损、目标、RR、仓位、质量闸门、交易计划、批量扫描
├── scoring/         9 因子、机会分、组合分、factor_weights（验证后的组合分权重）
├── market/          市场状态（真实指数）、宽度、风险
├── backtest/        交易计划回测、execution 成交与成本、metrics
├── ai/              AI 解读 + guard
├── research/        因子验证
├── screener.py / sector.py
├── holdings.py、holdings_quant.py、holdings_action.py、sell_zone.py、trade_monitor.py
├── calibration.py、shadow_orders.py、paper_tracking.py、portfolio_risk.py
├── notifier.py、scheduler.py、realtime.py、news.py、hithink.py、app_config.py
dashboard/           home.py + pages/0_opportunity、1_holdings、2_settings、3_factors
app/                 桌面壳
deploy/              ctl.py、restart.sh、ctl.sh
examples/            17 个可运行脚本
tests/               pytest 套件（以 pytest --collect-only 为准，README 不写死条数）
utils/app_meta.py    APP_VERSION
```

---

## 配置与安全

| 文件 | 说明 |
|------|------|
| `config/notify.yaml.example` | 复制为 `notify.yaml` 后填写；也可在配置页保存 |
| `config/holdings.yaml` | 旧持仓文件。首次使用且数据库为空时，会导入为同目录的 `holdings.db` |
| `config/users.yaml` | 看板登录。不存在时自动放行 |
| `config/hithink.env` | 同花顺 API Key（可选） |

不要提交 `notify.yaml`、真实 `holdings.db`、`hithink.env`，以及 SMTP / Server酱 / 飞书密钥（`.gitignore` 已覆盖）。

---

## A 股数据源（可选：同花顺）

A 股行情、估值、财务默认走 akshare（新浪 / 腾讯）。配置同花顺金融数据服务 API Key 后，A 股（含场内 ETF）会改走官方源；港股和美股不变。未配置或请求失败时回退默认源；鉴权失败会熔断 5 分钟。

配置方式：看板「配置」页，或 `config/hithink.env`，或环境变量 `HITHINK_FINANCE_API_KEY`（优先级最高）。Key 在 <https://fuyao.aicubes.cn/admin> 申请。安装包只带 `hithink.env.example`，不要把 Key 打进分发包。

其他来源：yfinance、Nasdaq screener。

---

## 平台

| 能力 | Windows | macOS | Linux |
|------|---------|-------|-------|
| `python deploy/ctl.py` | ✅ | ✅ | ✅ |
| `deploy/restart.sh` | 需 WSL / Git Bash | ✅ | ✅ |
| Streamlit 看板 | ✅ | ✅ | ✅ |
| `trade_monitor` 粘贴成交 | 视系统通知实现 | 偏 macOS 通知中心 | 视实现 |

全程用 `python deploy/ctl.py ...` 即可，不依赖 shell。

---

## 开发

```bash
pip install -e ".[all]"
pytest -q
ruff check stock_analysis dashboard utils examples
```

架构见 [`docs/architecture.md`](docs/architecture.md)，贡献约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。V2 设计文档（历史）：`docs/quant_trading_system_v2_dev_plan_zh.md` / `_en.md`。

## 许可证

[MIT](LICENSE)。仅供研究学习，**不构成投资建议**。

数据接口：同花顺金融数据服务（A 股可选）/ akshare（新浪）/ 腾讯行情 / yfinance / Nasdaq screener。
