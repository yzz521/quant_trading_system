# 阶段三增量：T+1 + 组合层单票暴露

## 覆盖文件（相对包根）

```
portfolio/manager.py          # Position.frozen / available_quantity；Portfolio.t1_enabled
risk/manager.py               # T+1 卖出限制 + 成交后投影权重上限
backtest/execution_handler.py # EXIT 仅平可用仓
backtest/engine.py            # BacktestConfig.t1_enabled 传入 Portfolio / RiskManager
tests/test_t1.py
tests/test_projected_weight.py
```

## 行为摘要

1. **T+1**（默认 `t1_enabled=True`）  
   - 当日买入计入 `frozen_quantity`  
   - `available_quantity = quantity - frozen`  
   - 新交易日（日期变大）在 `on_market` / `snapshot` 时解冻  
   - 风控拒绝「可卖数量不足」的卖出/EXIT  

2. **组合层单票权重**  
   - 在原有单笔 notional 限制外，增加「成交后持仓市值 / 权益」不得超过 `max_position_pct`  
   - 多策略共用同一 `Portfolio` 时，天然按合仓约束（解决叠加加仓）

3. **关闭 T+1**（美股/加密回测）  

```python
BacktestConfig(t1_enabled=False, lot_size=1)
```

## 验证

```bash
export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"
pytest -q tests/test_t1.py tests/test_projected_weight.py
```
