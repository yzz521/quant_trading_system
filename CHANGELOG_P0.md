# P0 工程基建补丁（feat overlay）

依据《综合改进计划书》阶段一落地，可直接覆盖到 `feat-20260731` / 本地工作区。

## 新增

- `LICENSE` — MIT
- `pyproject.toml` — 可编辑安装 + optional extras（data/ml/dashboard/live/dev）
- `tests/` — core 引擎、撮合成本、风控、组合记账、sell_zone 容错
- `.github/workflows/ci.yml` — pytest（+ ruff 可选）
- `config/holdings.yaml.example` — 无真实持仓的模板
- `CHANGELOG_P0.md` — 本说明

## 修改

- `README.md` — 增加「产品能力双轨」说明与测试入口
- `.gitignore` — 强化 IDE 忽略（若尚未包含）

## 未改动的业务逻辑

- 回测/策略/实盘骨架、stock_analysis 功能代码保持原样（含 sell_zone）

## 安全提醒

打包源中若曾包含 `config/notify.yaml`（邮箱授权码等），**本补丁包已删除该文件**。
若该文件曾推送到远端或分享给他人，请尽快在邮箱服务商处**重置 SMTP 授权码**，并轮换 Server酱等密钥。

## 本地使用

```bash
# 在本地 feat 分支目录（包根目录）解压覆盖后：
export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"   # 父目录名最好为仓库父级
pip install -e ".[dev]"   # 或 pip install pytest pandas numpy ...
pytest -q
```
