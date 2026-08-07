# 阶段七：策略注册表 + 深套卖出区间

## 文件

- `strategy/registry.py`（新建）
- `strategy/__init__.py`
- `stock_analysis/sell_zone.py` — `pnl<=-20%` 时区间改为「反弹位→回本」，止损改为再跌约 8%/布林下轨
- 测试：`tests/test_registry.py`、`tests/test_sell_zone_deep_loss.py`

## 深套逻辑摘要

| 状态 | 卖出区间 | 止损 |
|------|----------|------|
| 盈利 | 现价上方压力/止盈锚点 | MA20 与成本附近 |
| 浅亏 | 回本 + 技术压力 | MA20 或再跌 5% |
| **深套 ≤-20%** | **途中反弹位 → 回本价** | **再跌 ~8% 或布林下轨** |

Dashboard 无需改代码即可吃到新 `advice` / `regime` 字段（表格仍用 zone_lo/hi）。
