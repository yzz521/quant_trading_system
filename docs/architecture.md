# 架构说明

## 双轨产品

| 轨道 | 职责 | 入口 |
|------|------|------|
| A. 事件驱动框架 | 回测 / Paper / 策略 / 风控 | `BacktestEngine`, `examples/` |
| B. A 股助手 | 持仓 SQLite、卖出区间、推送 | `dashboard/holdings_app.py`, `deploy/ctl.sh` |

两轨共享：`core` 事件模型、部分行情工具；**助手不会自动下真实订单**。

## 事件流（回测）

```
BarFeed → MARKET → Strategy → SIGNAL → ExecutionHandler
         → RiskManager → ORDER → SimulatedBroker → FILL → Portfolio
```

- 默认 `fill_policy=next_open`，避免偷看当根收盘价。
- `EventEngine(thread_safe=False)` 用 `deque`；Paper/Live 用 `thread_safe=True`（`queue.Queue`）。

## 关键模块

| 目录 | 作用 |
|------|------|
| `core/` | Event、Engine |
| `data/` | DataSource、BarFeed、quality |
| `strategy/` | 策略 + `create_strategy` 注册表 |
| `risk/` | 仓位/回撤/T+1 可卖量/投影权重 |
| `portfolio/` | 现金、持仓、权益曲线、T+1 frozen |
| `backtest/` | Broker、Optimizer（grid/walk-forward） |
| `execution/` | Paper/Live broker 骨架 |
| `stock_analysis/` | 持仓 DB、卖出区间、调度推送 |
| `dashboard/` | Streamlit UI |

## 持仓桥接

`stock_analysis/holdings_bridge.py`：

- `apply_holdings_to_portfolio`：真实持仓 → 框架 Portfolio（研究/Paper）
- `portfolio_to_holdings_rows`：Portfolio → 助手行格式
- `snapshot_compare`：两边数量对账

## A 股约束（回测可配）

- T+1：`BacktestConfig.t1_enabled`
- 涨跌停 / 量能：`enforce_limit` / `enforce_volume`
- 印花税卖出单边、`lot_size=100`
