# 架构说明（main-v3）

`main-v3` 是每日投研助手，不是事件驱动交易框架。V1 的 `core/`、`data/`、`strategy/`、`risk/`、`portfolio/`、`execution/` 以及 `SignalEvent` 都在 `main` 分支，本分支没有这些目录。`docs/LIVE_BROKERS.md` 描述的券商适配器同样不在本分支。

本分支**不会向券商发送委托**。`stock_analysis/shadow_orders.py` 只把计划写成订单意图并过风控闸门，台账是 append-only 的。

## 一条候选怎么变成计划

```
全市场快照（screener.py）
  → 板块强度（sector.py）
  → 9 因子个股分 + 机会分 + 组合分（scoring/）
  → 质量闸门（opportunity/quality_gate.py）
  → 交易计划：入场 / 止损 / 三档目标 / RR / 仓位（opportunity/）
  → 看板、邮件、可选的 AI 解读（ai/guard.py 先校验再展示）
```

未设置预计投入金额时，看板「今日机会」不扫描（仓位要用这个金额）。

## 目录

| 路径 | 作用 |
|------|------|
| `stock_analysis/screener.py` | A 股 / 港股 / 美股初筛 |
| `stock_analysis/sector.py` | 新浪行业强度与成分映射；未命中中性 50 |
| `stock_analysis/scoring/` | 9 因子个股分、机会分、组合分；`factor_weights.py` 按验证报告调整**组合分**权重 |
| `stock_analysis/opportunity/` | 支撑阻力、入场、止损、目标、RR、仓位、质量闸门、批量扫描 |
| `stock_analysis/market/` | 用真实指数判断市场状态，并给出仓位系数 |
| `stock_analysis/backtest/` | 交易计划回测（防未来函数）与成交/成本模型 |
| `stock_analysis/research/factor_validation.py` | IC/IR、分位单调性、滚动前瞻 |
| `stock_analysis/ai/` | AI 解读 + 数值白名单 / 决策一致性 / 免责声明守卫 |
| `stock_analysis/calibration.py` | 把置信度打分映射成概率（Platt / isotonic；样本不足则不标定） |
| `stock_analysis/shadow_orders.py` | 影子模式：5 道风控闸门，不下单 |
| `stock_analysis/paper_tracking.py` | 纸面跟踪：信号与结算分两个 append-only JSONL |
| `stock_analysis/portfolio_risk.py` | 组合集中度、行业暴露、相关性、VaR/CVaR、回撤熔断 |
| `stock_analysis/holdings*.py`、`sell_zone.py`、`trade_monitor.py` | 持仓账本、卖出区间、粘贴成交同步 |
| `stock_analysis/notifier.py`、`scheduler.py`、`realtime.py` | 邮件 / Server酱 / 飞书，交易日调度，盘中盯盘（默认关） |
| `dashboard/home.py` + `dashboard/pages/` | Streamlit：今日机会、持仓、配置、因子验证 |
| `app/` | 桌面壳（pywebview + PyInstaller） |
| `deploy/` | `ctl.py`（跨平台）与 `restart.sh` / `ctl.sh` |
| `examples/` | 可运行脚本（扫描、回测、邮件预览、因子验证、影子模式等） |
| `utils/app_meta.py` | `APP_VERSION` 与 GitHub Releases 地址 |

## 看板页面

Streamlit 按文件名排序加载 `dashboard/pages/`：

| 文件 | 页面 |
|------|------|
| `0_opportunity.py` | 今日机会 |
| `1_holdings.py` | 持仓指挥台 |
| `2_settings.py` | 配置（邮件、市场、扫描、同花顺 Key、检查更新、实时盯盘） |
| `3_factors.py` | 因子验证（IC/IR、分位、滚动前瞻、生效权重） |

入口是 `dashboard/home.py`，默认端口 8502。

## 回测约束（本分支的实现）

见 `stock_analysis/backtest/trading_plan_backtest.py` 与 `execution.py`：

- 计划只用截至 T 日的 K 线；结果只用 T 日之后的 K 线。
- `OpportunityEngine` 默认 `fetch_news=False`，避免把当天公告套到历史 K 线。
- 限价买入必须触及委托价；扣除佣金、印花税、过户费和滑点。
- 跳空时止损/目标按开盘价成交；涨跌停与停牌日不成交；单日成交额参与率有上限。

这与 V1 的 `fill_policy=next_open`、`EventEngine`、T+1 `BacktestConfig` 不是同一套代码。
