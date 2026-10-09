# 贡献指南

默认分支是 `main-v3`：每日投研助手（筛选 → 评分 → 交易计划 → 回测 → AI 解读）。V1 事件驱动框架在 `main`，不要把 `SignalEvent`、`core/`、`strategy/` 写回本分支的文档或代码，除非单独开一个明确的迁移。

## 环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
# 与 CI 相同、不含桌面壳：pip install -e ".[dev,data,dashboard]"
pytest -q
ruff check stock_analysis dashboard utils examples
```

仓库根目录就是包目录。本地开发若导入失败，把**上一级目录**加进 `PYTHONPATH`（CI 也是这么做的）。

## 分支与提交

- 小步 PR：一个主题一次合入。
- 提交说明用中文或英文均可，写清为什么改。
- **不要提交** `config/notify.yaml`、真实 `holdings.db`、`config/hithink.env`、密钥、`dist/`、`output/`、`.venv`。

## 代码约定

- 大模型不定价。展示 AI 文本前走 `stock_analysis/ai/guard.py`（数值白名单、决策一致性、免责声明）；不通过就退回规则解读。
- 回测默认没有未来函数：计划与结果分属 T 日两侧；回测里不要打开 `fetch_news`。
- 新增风控、闸门或成交口径必须带单测。
- 助手侧单票失败要降级（记入 `error`），不要让整页或整封邮件崩溃。
- 本分支不发送真实委托。影子模式只写意图。

## 文档

- 架构：`docs/architecture.md`（描述当前分支，不是 V1）。
- 中文 README 是仓库首页；英文在 `README_EN.md`，两边的事实要一致。
- 公开说明保持「研究工具、不构成投资建议、不下真实单」，不要写成自动实盘。
