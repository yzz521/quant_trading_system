# 阶段八：深套两段目标 + 持仓桥接 + 文档

## 文件

- `stock_analysis/sell_zone.py` — stage1/stage2 字段与文案
- `dashboard/holdings_app.py` — 分批路径展示
- `stock_analysis/holdings_bridge.py` — 新建
- `docs/architecture.md`、`CONTRIBUTING.md`
- 测试：`tests/test_holdings_bridge.py`、`tests/test_sell_zone_stages.py`

## 重启看板

```bash
./deploy/ctl.sh dashboard restart
```
