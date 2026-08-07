# GP助手 × 本地 Vibe-Trading

## 仓库 / 来源

- 官方仓库：[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（HKU 开源的
  “个人交易 Agent”框架，提供会话式研究、行情工具与策略沙箱）
- 本文档描述 GP助手如何把本地事实（持仓 + 扫描候选）投喂给该 Agent 做二次分析

## 架构

- **主**：quant_trading_system（持仓事实、纪律）
- **从**：本机 `vibe-trading serve --port 8899`
- **展示**：看板「Vibe 二次分析」页 + `results/vibe/`

## 启动

```bash
# 终端1 — Vibe
pip install vibe-trading-ai   # 若未装
vibe-trading init            # 首次
vibe-trading serve --port 8899

# 终端2 — GP助手
python deploy/ctl.py start-all
# 打开 http://localhost:8502 → 左侧「Vibe 二次分析」
```

## CLI

```bash
python examples/run_vibe_secondary.py
```

## 说明

- Vibe API 字段可能随版本变化；桥接层宽松解析。
- 连不上时仍写入 `results/vibe/payload_*.json`，可粘贴到 Vibe UI。
- 不向 Vibe 发送邮箱密码或券商凭证。
