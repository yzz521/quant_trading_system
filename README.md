# quant_trading_system · GP助手

**main-v3 精简版** —— 每日投资决策助手，只保留三块核心功能：
**今日计划**（全市场筛选 → 板块轮动 → 9 因子评分 → 交易计划 + 回测验证 + AI 解读）、**我的持仓**、**持仓卖出/加仓参考**，支持每日邮件推送与本地看板。

- **License**: MIT
- **Python**: ≥ 3.10
- **仓库**: https://github.com/yzz521/quant_trading_system（默认分支 `main-v3`）
- 历史版本：`main-v2`（V2 完整版，含诊断/扫描/漏斗/Vibe）、`main`（V1 事件驱动量化框架）

---

## 功能一览

| 功能 | 说明 | 入口 |
|------|------|------|
| 🎯 **今日计划** | 全市场初筛（A股5542/港股/美股7000+）→ 板块轮动 → 9 因子评分 → 交易计划（入场区间/止损/三档目标/RR/仓位）→ 历史回测 → AI 解读；真实指数市场状态调节仓位。**未设置预计投入金额时不扫描** | 看板「今日机会」页、`examples/run_opportunity.py` |
| 💼 **我的持仓** | SQLite 持仓管理（增删改、加权成本）、盈亏计算、粘贴成交自动同步 | 看板「持仓与卖出区间」页、`examples/my_holdings.py` |
| 🎯 **卖出/加仓参考** | 卖出一二档、止损、深套分批路径、加仓参考 | 看板持仓页、每日邮件区块 |
| 📧 **每日邮件** | 持仓 + 资金账户 + 今日机会 + 卖出/加仓参考 四区块，交易日自动推送 | 看板「配置」页开关邮件、`examples/run_scheduler.py` |
| ⚙️ **配置** | 是否发邮件、收件地址、监测 A股/港股/美股、扫描参数；打包版可检查并安装 GitHub 新版本 | 看板「配置」页（侧栏 `settings`） |

决策状态：🟢BUY_NOW / 🟢BUY_ON_PULLBACK / 🟡WATCH / 🟠HOLD / 🔴SELL / ⛔AVOID（RR<1.5 即 AVOID，不计算仓位）。量化负责计算、AI 负责解释、回测负责验证、**你做最终决策**。

---

## 筛选流水线（今日推荐怎么来的）

```
全市场快照（秒级）
   ↓ ① Hard Filter：成交额下限 + 涨跌幅区间 + 名称剔除(*ST/退/新股)
Top N 候选（默认 30，可调 5~80）
   ↓ ② 板块轮动：新浪 49 行业强度排名（涨跌幅60%+成交额40%百分位）
   ↓ ③ 9 因子评分（权重和=1.00）
        fundamental .12  growth .08  technical .20  momentum .05
        capital_flow .15  valuation .10  market_env .05  sector .05  risk .20
   ↓ ④ 机会引擎：支撑阻力 → 入场 → 止损/目标 → RR → 仓位 → 决策
🟢 买入列表（BUY_NOW / BUY_ON_PULLBACK）  🟡 关注列表（WATCH）
```

- **初筛**（`screener.py`）：全市场快照一次拉取，不拉 K 线秒级完成。A股/港股按成交额过滤；美股用 nasdaq 官方 API（市值≥100亿美元），失败回退知名池
- **板块轮动**（`sector.py`）：新浪 49 行业强度 + 全市场成分映射（24h 缓存），强势板块候选加分；未命中中性 50
- **9 因子评分**（`scoring/`）：Growth 成长（营收/净利同比，单票详情拉取）、Momentum 动量（20/60日收益+RSI）、Sector 板块强度为 main-v3 新增。技术趋势以均线结构为主，MACD/ADX 做方向与强度确认；RSI/KDJ/CCI/WR 只作超买风险过滤，不另开因子。K 线形态写入机会分的「相似形态」槽（10%）；斐波那契回撤参与支撑/阻力与入场基准。信息面（近 14 日公告+新闻关键词）并入 **risk 20%** 同一票：减持/立案等降分、回购/中标等小幅加分；回测默认不拉新闻，避免把今日公告套到历史K线
- **决策**：RR<1.5 → AVOID 过滤；其余按机会分排序展示，看板分「买入列表/关注列表」双 tab

---

## 快速开始

### 0. 桌面应用（推荐，可安装）

把看板 + 定时推送打包成原生桌面应用（macOS / Windows / Linux），双击即用、
无需终端和浏览器标签页，关窗即停调度：

```bash
# 本地打包（调试用）
pip install -e ".[data,dashboard,gui]" pyinstaller
pyinstaller app/packaging/gp_assistant.spec --noconfirm
```

**自动构建（推荐）**：push `v*` tag 即触发 GitHub Actions 三端自动打包，
产物自动上传 GitHub **Releases 页直接下载**（无需本地 Python/PyInstaller）：

```bash
git tag v0.3.10 && git push origin v0.3.10
# → Releases：GP-Assistant-macOS-arm64/x64.zip、GP-Assistant-Windows.zip、GP-Assistant-Linux.tar.gz
```

下载后：

- **Windows**：解压整个 `GPAssistant` 文件夹，双击 `GPAssistant.exe`。若 SmartScreen 提示「已保护你的电脑」→「更多信息」→「仍要运行」。不要只拷贝 exe。
- **macOS（无需 Apple 开发者账号 / 公证）**：解压后双击 `首次打开.command`（会去掉隔离属性并做本机临时签名）。或在终端执行：
  `xattr -cr GP助手.app && codesign --force --deep --sign - GP助手.app && open GP助手.app`

macOS 产物 `dist/GP助手.app`；Windows 产物 `dist/GPAssistant/GPAssistant.exe`。
详见 [`app/README.md`](app/README.md)。

### 1. 安装（服务模式）

```bash
git clone https://github.com/yzz521/quant_trading_system.git
cd quant_trading_system
git checkout main-v3

python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
pip install akshare streamlit
# 或: pip install -r requirements.txt && pip install streamlit akshare
```

### 2. 一键启动（macOS，推荐）

```bash
./deploy/restart.sh            # 重启 launchd 调度器 + Streamlit 看板
./deploy/restart.sh dashboard  # 只重启看板
./deploy/restart.sh scheduler  # 只重启调度器
./deploy/restart.sh status     # 查看运行状态与最近日志
```

- 看板：http://localhost:8502（「今日机会」+「持仓与卖出区间」+「配置」）
- 调度器：launchd `com.gp.stock-scheduler`，交易日开盘时段定时推送邮件
- 其他平台：`python deploy/ctl.py dashboard start` / `python deploy/ctl.py scheduler start`

### 3. 运行测试与示例

```bash
pytest -q                          # 147 项测试
ruff check stock_analysis dashboard utils examples

# 单票交易计划（联网，默认 600000）
python examples/run_opportunity.py 600000 --account 100000
# 离线演示（合成数据）
python examples/run_opportunity.py --synthetic

# 批量机会扫描（候选池 → 按机会分排序的计划列表）
python examples/run_batch_opportunity.py 600000 000001 600519

# 历史规则回测（验证入场/止损/目标是否有效，防 look-ahead）
python examples/run_backtest_plan.py 600000 --days 750

# 持仓
python examples/my_holdings.py
# 邮件模板预览（写 results/email_*_preview.html）
python examples/gen_email_preview.py
```

---

## 每日邮件（定时推送）

1. 复制并编辑推送配置（**不要**提交真实密钥），或直接在看板「配置」页填写：

```bash
cp config/notify.yaml.example config/notify.yaml
# 看板「配置」页可开关发信、填写邮箱、勾选监测市场（A股/港股/美股）
# 也可手改 notify.yaml：notify.email.enabled + SMTP 授权码与收件人
# opportunity.enabled=true 后，邮件将包含「今日机会 · 交易计划」区块
#   index_symbol: 市场状态参考指数（sh000001 上证 / sh000300 沪深300）
#   max_stocks: 全市场初筛候选上限 / account_equity / workers / min_opportunity_score
# enabled_markets: 监测并推送的市场，缺省仅 A股
```

2. 测试一发（可指定市场，不依赖是否开盘）：

```bash
python examples/run_scheduler.py --test --market CN
```

3. 常驻：

```bash
./deploy/restart.sh scheduler
# 或
python deploy/ctl.py scheduler start
```

推送内容四区块：**💼 我的持仓 → 💰 资金账户 → 🎯 今日机会 · 交易计划 → 🎯 持仓卖出/加仓参考**。日志：`results/scheduler.log`。

---

## 目录结构

```text
quant_trading_system/
├── stock_analysis/          # 核心逻辑
│   ├── opportunity/         # 支撑阻力/入场/止损/目标/RR/仓位/TradingPlan/机会引擎/批量扫描
│   ├── scoring/             # 9 因子 Stock Score + Opportunity Score（可交易性）
│   ├── market/              # 市场状态（真实指数）/ 宽度 / 风险
│   ├── backtest/            # Trading Plan 历史回测（严格防 look-ahead）
│   ├── ai/                  # AI 分析师（量化结果 → 自然语言，只解释不定价）
│   ├── screener.py          # 全市场初筛器（A股/港股/美股）
│   ├── sector.py            # 板块轮动（新浪 49 行业强度 + 成分映射）
│   ├── holdings.py          # 我的持仓（SQLite）
│   ├── sell_zone.py         # 卖出区间（含深套分批路径）
│   ├── holdings_action.py   # 卖出/加仓参考
│   ├── trade_monitor.py     # 粘贴成交解析 + 同步持仓（parser + apply_trade）
│   ├── data_fetcher.py      # 多市场行情（A股/美股/港股，多源降级 + 成长因子）
│   ├── indicators.py        # 技术指标（MA/MACD/RSI/KDJ/BOLL/ATR/ADX/VWAP/斐波那契）
│   ├── patterns.py          # K线形态（吞没/晨暮星/三兵三鸦等，接入机会分相似形态）
│   ├── news.py              # 信息面（东财公告+新闻关键词，接入个股分 risk）
│   ├── notifier.py          # 邮件/Server酱/飞书推送
│   ├── scheduler.py         # 交易时段调度器
│   └── app_config.py        # 看板配置页读写 notify.yaml
├── dashboard/               # Streamlit：首页 + 今日机会 + 持仓 + 配置
│   └── pages/               # 0_opportunity / 1_holdings / 2_settings（ASCII 文件名）
├── app/                     # 桌面应用壳（pywebview + PyInstaller）
├── deploy/                  # restart.sh / ctl.py（跨平台管理）
├── examples/                # 7 个冒烟脚本
├── config/                  # notify.yaml.example、holdings.yaml（勿提交真实密钥）
├── tests/                   # 147 项测试
└── docs/
```

---

## 配置与安全

| 文件 | 说明 |
|------|------|
| `config/notify.yaml.example` | 复制为 `notify.yaml` 后填推送凭证；也可在看板「配置」页保存（SMTP/Server酱/飞书/ai/监测市场） |
| `config/holdings.yaml` / `.db` | 持仓配置与 SQLite 数据（本地） |
| `config/users.yaml` | 看板登录（不存在时自动放行） |

**不要**把 `notify.yaml`、真实 `holdings.db`、SMTP / Server酱 / 飞书密钥提交到 Git（`.gitignore` 已覆盖）。

---

## 平台说明

| 能力 | Windows | macOS | Linux |
|------|---------|-------|-------|
| `python deploy/ctl.py` | ✅ | ✅ | ✅ |
| `deploy/restart.sh` | 需 WSL/Git Bash | ✅ | ✅ |
| Streamlit 看板 | ✅ | ✅ | ✅ |
| `trade_monitor` 粘贴成交 | 视实现 | 偏 macOS | 视实现 |

**不依赖 shell 脚本**：全程使用 `python deploy/ctl.py ...` 即可管理服务。

---

## 开发

```bash
pip install -e ".[dev]"
pytest -q
ruff check stock_analysis dashboard utils examples
```

V2 设计文档（中英）：`docs/quant_trading_system_v2_dev_plan_zh.md` / `_en.md`。

---

## 致谢

数据接口：akshare（新浪/同花顺）/ yfinance / nasdaq screener / 腾讯行情。仅供研究学习，不构成投资建议。
