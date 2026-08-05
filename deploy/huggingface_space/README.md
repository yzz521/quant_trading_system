---
title: Quant Trading System
emoji: 👁
colorFrom: green
colorTo: purple
sdk: streamlit
sdk_version: 1.37.1
app_file: "dashboard/首页.py"
python_version: "3.12"
pinned: false
---

# Quant Trading System · 公网看板

Hugging Face Space 部署副本（由本仓库 `deploy/huggingface_space/` 生成）。

**运行方式**：`sdk: streamlit`，入口 `dashboard/首页.py`，依赖见 `requirements.txt`。

注意：
- `config/users.yaml` 已包含账号门禁（哈希密码），无此文件时登录不拦截。
- 请勿向此公开 Space 提交个人持仓数据（`config/holdings.db`、`config/holdings.yaml`）或邮件凭证（`config/notify.yaml`）。
- 免费 Space 空闲后会休眠，重启后容器内写入的数据会丢失。
