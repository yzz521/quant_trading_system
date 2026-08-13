# quant_trading_system · GP助手

**main-v3 精简版** —— 每日投资决策助手，只保留三块核心功能：
**今日计划**（评分/入场/止损/目标/风险收益比/仓位 + 回测验证 + AI 解读）、**我的持仓**、**持仓卖出/加仓参考**，支持每日邮件推送与本地看板。

- **License**: MIT
- **Python**: ≥ 3.10
- **仓库**: https://github.com/yzz521/quant_trading_system（默认分支 `main-v3`）
- 历史版本：`main-v2`（V2 完整版，含诊断/扫描/漏斗/Vibe）、`main`（V1 事件驱动量化框架）

---

## 功能一览

| 功能 | 说明 | 入口 |
|------|------|------|
| 🎯 **今日计划** | 个股双评分（Stock / Opportunity）→ 机会引擎 → 交易计划（入场区间/止损/三档目标/RR/仓位）→ 历史回测 → AI 解读；真实指数市场状态（BULL/NEUTRAL/BEAR/HIGH_RISK）调节仓位 | 看板「今日机会」页、`examples/run_opportunity.py` |
| 💼 **我的持仓** | SQLite 持仓管理（增删改、加权成本）、盈亏计算、粘贴成交自动同步 | 看板「持仓与卖出区间」页、`examples/my_holdings.py` |
| 🎯 **卖出/加仓参考** | 卖出一二档、止损、深套分批路径、加仓参考 | 看板持仓页、每日邮件区块 |
| 📧 **每日邮件** | 持仓 + 资金账户 + 今日机会 + 卖出/加仓参考 四区块，交易日自动推送 | `examples/run_scheduler.py` |

决策状态：🟢BUY_NOW / 🟢BUY_ON_PULLBACK / 🟡WATCH / 🟠HOLD / 🔴SELL / ⛔AVOID（RR<1.5 即 AVOID，不计算仓位）。量化负责计算、AI 负责解释、回测负责验证、**你做最终决策**。

---

## 快速开始

### 1. 安装

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

- 看板：http://localhost:8502（「今日机会」+「持仓与卖出区间」两页）
- 调度器：launchd `com.gp.stock-scheduler`，交易日开盘时段定时推送邮件
- 其他平台：`python deploy/ctl.py dashboard start` / `python deploy/ctl.py scheduler start`

### 3. 运行测试与示例

```bash
pytest -q                          # 96 项测试
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

1. 复制并编辑推送配置（**不要**提交真实密钥）：

```bash
cp config/notify.yaml.example config/notify.yaml
# 打开 notify.email.enabled，填写 SMTP 授权码与收件人
# opportunity.enabled=true 后，邮件将包含「今日机会 · 交易计划」区块
#   index_symbol: 市场状态参考指数（sh000001 上证 / sh000300 沪深300）
#   max_stocks / account_equity / workers / min_opportunity_score
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
│   ├── scoring/             # Stock Score（个股质量）+ Opportunity Score（可交易性）
│   ├── market/              # 市场状态（真实指数）/ 宽度 / 风险
│   ├── backtest/            # Trading Plan 历史回测（严格防 look-ahead）
│   ├── ai/                  # AI 分析师（量化结果 → 自然语言，只解释不定价）
│   ├── holdings.py          # 我的持仓（SQLite）
│   ├── sell_zone.py         # 卖出区间（含深套分批路径）
│   ├── holdings_action.py   # 卖出/加仓参考
│   ├── trade_monitor.py     # 粘贴成交解析 + 同步持仓（parser + apply_trade）
│   ├── data_fetcher.py      # 多市场行情（A股/美股/港股，多源降级）
│   ├── indicators.py        # 技术指标（MA/MACD/RSI/KDJ/BOLL/ATR 等）
│   ├── notifier.py          # 邮件/Server酱/飞书推送
│   └── scheduler.py         # 交易时段调度器
├── dashboard/               # Streamlit：首页 + 今日机会 + 持仓与卖出区间
├── deploy/                  # restart.sh / ctl.py（跨平台管理）
├── examples/                # 7 个冒烟脚本
├── config/                  # notify.yaml.example、holdings.yaml（勿提交真实密钥）
├── tests/                   # 96 项测试
└── docs/
```

---

## 配置与安全

| 文件 | 说明 |
|------|------|
| `config/notify.yaml.example` | 复制为 `notify.yaml` 后填推送凭证（SMTP/Server酱/飞书/ai） |
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

数据接口：akshare / yfinance / 新浪 / 腾讯行情。仅供研究学习，不构成投资建议。
