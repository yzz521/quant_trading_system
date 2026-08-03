# 贡献指南

## 环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,data]"   # 或 pip install pytest pandas numpy pyyaml streamlit akshare
export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"   # 若包目录名为 quant_trading_system
pytest -q
```

## 分支与提交

- 小步 PR：一个主题（T+1、卖出区间、文档…）一次合入。
- 提交说明用中文或英文均可，建议写清「行为变化」。
- **不要提交** `config/notify.yaml`、真实 `holdings.db`、密钥、本地 `.venv`。

## 代码约定

- 策略只发 `SignalEvent`，不直接改 Portfolio。
- 回测默认无未来函数（`next_open`）。
- 新增风控/撮合行为必须带单测。
- 助手侧失败要降级（单票 `error`），不要让整页崩溃。

## 文档

- 架构：`docs/architecture.md`
- 公开 README 保持「双轨」说明，避免把助手写成自动实盘。
