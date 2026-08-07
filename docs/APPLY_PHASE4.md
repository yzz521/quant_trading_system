# 阶段四增量：涨跌停 + 成交量约束 + 数据质量工具

## 覆盖文件

```
backtest/broker.py
backtest/engine.py
data/quality.py          # 新建；需在 data/__init__.py 按需导出（可选）
tests/test_broker_limit_volume.py
tests/test_data_quality.py
```

## 配置（BacktestConfig）

| 参数 | 默认 | 含义 |
|------|------|------|
| `limit_pct` | 0.10 | 涨跌停幅度（主板 10%；ST 可改 0.05） |
| `enforce_limit` | True | 涨停拒买、跌停拒卖 |
| `max_volume_pct` | 0.25 | 单笔不超过当根 bar volume 的比例 |
| `enforce_volume` | True | 启用成交量上限 |

关闭示例：

```python
BacktestConfig(enforce_limit=False, enforce_volume=False)
```

## 说明

- 涨跌停判定依赖 broker 内部记录的 **前收**；第一根 bar 无前收时不做涨跌停拒绝。
- 被拒绝的订单 **不重新排队**（当日未成交即作废），与简化回测一致。
- `data/quality.py` 默认不接入取数路径，可在策略/示例中手动调用。
