# 量化交易系统改进计划书

> 项目：[`yzz521/quant_trading_system`](https://github.com/yzz521/quant_trading_system)  
> 分析日期：2026-07-31  
> 当前版本：`0.1.0`（今日 init + 若干个人持仓/通知增强）

---

## 一、项目现状摘要

### 1.1 架构亮点（值得保留）

| 维度 | 评价 |
|------|------|
| 事件驱动核心 | `EventEngine` + 不可变 `Event`/`Bar` 设计清晰，回测与实盘共用同一总线，策略零改动切换理念正确 |
| 无前视偏差 | 默认 `fill_policy=next_open`，符合严肃回测要求 |
| 分层解耦 | data / strategy / risk / portfolio / backtest / execution / analytics 边界清楚 |
| 可插拔 | DataSource、Sizer、Broker 均可替换 |
| 成本模型 | 佣金、印花税、滑点、最低佣金可配置 |
| 个人交易助手 | `stock_analysis`（诊断、扫描、调度、通知）+ 持仓 SQLite + macOS 成交监听，实用性强 |
| 离线能力 | `SyntheticDataSource` 保证无网可跑 demo |

### 1.2 主要短板（按优先级）

1. **工程化严重不足**：无 `tests/`、无 `pyproject.toml`/`setup.py`、无 CI、无 LICENSE、依赖未分层 extras  
2. **双产品定位模糊**：README 主打「事件驱动量化框架」，但大量代码是 A 股个人助手（持仓、邮件推送、trade_monitor），文档与目录未清晰拆分  
3. **实盘骨架过虚**：CTP / IBKR / Binance 仅抛 `NotImplementedError`  
4. **回测完备性不足**：缺少 T+1、涨跌停、停牌、成交量约束、分笔撮合、参数寻优、Walk-Forward  
5. **可观测与质量**：无单元/集成测试、无类型检查门禁、日志与指标未结构化  
6. **ML 模块偏 demo**：特征与模型较薄，缺少严格的时间序列交叉验证与泄漏防护文档化  

---

## 二、改进目标（6 个月视角）

- **P0**：可安装、可测试、可 CI，新人 5 分钟跑通 + 有测试护栏  
- **P1**：回测更接近真实 A 股规则；文档与产品边界清晰  
- **P2**：Paper 交易闭环可用；一种实盘对接打通（优先 A 股或模拟盘）  
- **P3**：研究工具（寻优、Walk-Forward、因子研究）与个人助手深度打通  

---

## 三、分阶段改进计划

### 阶段 0：工程基建（1–2 周）— 必须先做

| 序号 | 事项 | 说明 | 验收标准 |
|------|------|------|----------|
| 0.1 | 引入 `pyproject.toml` | 用 setuptools/hatchling；`[project.optional-dependencies]` 拆分 `data` / `ml` / `dashboard` / `live` | `pip install -e ".[data,ml]"` 成功 |
| 0.2 | 包布局规范化 | 推荐 `src/quant_trading_system/`，或明确「仓库根即包」并写清安装方式；避免 `cd quant_trading_system && python examples/...` 路径混乱 | README 安装步骤一次成功 |
| 0.3 | 添加 LICENSE | 建议 MIT 或 Apache-2.0，与「学习研究」免责一致 | 根目录有 LICENSE |
| 0.4 | 测试框架 | `pytest` + `pytest-cov`；先覆盖 core / portfolio / risk / broker 成交逻辑 | 覆盖率 core+backtest ≥ 60% |
| 0.5 | CI | GitHub Actions：lint（ruff）+ type（可选 mypy）+ pytest | PR 必须绿 |
| 0.6 | 依赖钉版本策略 | requirements 或 lock；可选 `uv`/`poetry` | 可复现安装 |
| 0.7 | 示例脚本路径 | `python -m quant_trading_system.examples.backtest_demo` 或 console_scripts | 不依赖 cwd |

**建议测试用例（优先）：**

- `next_open` 成交价格与时间正确  
- 印花税仅卖出、最低佣金、lot_size 取整  
- 风控：超仓位 / 超暴露 / 回撤熔断拒绝开仓  
- 合成数据回测净值可复现（固定 seed）  

---

### 阶段 1：文档与产品边界（1 周，可与阶段 0 并行）

| 序号 | 事项 | 说明 |
|------|------|------|
| 1.1 | README 双轨说明 | 明确两块能力：**A. 事件驱动回测/交易框架**；**B. A 股个人持仓与扫描助手**（`stock_analysis` + holdings dashboard） |
| 1.2 | 架构文档 | `docs/architecture.md`：事件流时序图、组件职责、回测 vs 实盘差异 |
| 1.3 | 开发者指南 | 如何写策略、如何加 DataSource、如何加风控规则 |
| 1.4 | 配置说明 | `settings.yaml` / `notify.yaml` 字段表；敏感信息禁止进仓 |
| 1.5 | 更新目录树 | README 中补上 `stock_analysis`、`deploy`、`dashboard/holdings_app` 等已有模块 |

---

### 阶段 2：回测真实性与研究能力（3–4 周）

| 序号 | 事项 | 优先级 | 说明 |
|------|------|--------|------|
| 2.1 | A 股交易规则 | P0 | T+1（当日买入不可卖）、涨跌停无法成交或部分成交、停牌跳过、最小交易单位 100 股 |
| 2.2 | 成交量约束 | P1 | 订单量不超过当根 bar volume 的一定比例（可配置） |
| 2.3 | 多时间框架 | P1 | 明确 1d 为主；预留 1m/5m 的 feed 对齐与时钟 |
| 2.4 | 参数网格 / 随机搜索 | P1 | `backtest/optimizer.py`：对策略 params 网格，输出表格 + 防过拟合提示 |
| 2.5 | Walk-Forward | P1 | 滚动训练/测试窗口（尤其 ML 策略） |
| 2.6 | 基准与超额 | P2 | 相对沪深 300 / 中证 500 的超额收益、信息比率 |
| 2.7 | 交易明细导出 | P2 | 每笔成交 CSV/Parquet，便于审计 |

**风控增强建议：**

- 单票日内亏损熔断  
- 行业/板块暴露上限（依赖行业映射数据）  
- 订单频率限制（防策略 bug 连发）  

---

### 阶段 3：数据层加固（2 周）

| 序号 | 事项 | 说明 |
|------|------|------|
| 3.1 | 统一 OHLCV schema | 强制列名、时区、复权标记（qfq/hfq/none）写进 DataSource 契约 |
| 3.2 | 缓存策略 | 现有 DiskCache 很好；补充缓存失效、增量更新、元数据（下载时间、源） |
| 3.3 | 多源降级 | AkShare 失败时可选 Tushare / 本地 parquet |
| 3.4 | 交易日历 | A 股 / 美股日历接口，回测只在交易日推进 |
| 3.5 | 基本面/财务（可选） | 若多因子要走真因子，需独立模块，勿与行情混用 |

---

### 阶段 4：执行与实盘路径（4–6 周，分市场）

**原则：先 Paper 闭环，再一种真实通道。**

| 路径 | 建议顺序 | 说明 |
|------|----------|------|
| PaperBroker | 已有基础 | 接实时/准实时 bar（定时拉 AkShare），策略真正跑起来并写成交日志 |
| A 股 | 优先个人场景 | 若继续 GUI/通知驱动，可强化 trade_monitor + 手动确认；真 API 需合规券商接口 |
| 期货 CTP | 中期 | 基于 vnpy_ctp 填完骨架，Simnow 验证 |
| 加密 Binance | 相对容易 | Testnet 先跑通下单/查询/成交回调 |
| IBKR | 美股用户再做 | ib_insync Paper 账户 |

**Live 引擎必补：**

- 线程安全事件队列（`queue.Queue` + 锁）  
- 断线重连、心跳、日切重置  
- 启动时持仓/资金与券商对账  
- 所有实盘默认「只读 / 模拟」开关，防止误下单  

---

### 阶段 5：个人助手与框架打通（2–3 周）

当前 `stock_analysis` 与核心引擎相对独立，建议：

| 序号 | 事项 |
|------|------|
| 5.1 | 持仓 SQLite 与回测 Portfolio 模型字段对齐，便于「实盘持仓 → 风险诊断」 |
| 5.2 | 扫描结果一键生成「待回测 universe」 |
| 5.3 | 调度器任务与 EventEngine 解耦文档化（避免两套运行时混用） |
| 5.4 | trade_monitor 平台抽象：macOS 通知为一种 adapter，预留 Windows/手动粘贴 |
| 5.5 | Dashboard 统一入口：回测 / 持仓 / 个股诊断 三个页签，避免多个 `*_app.py` 散落 |

---

### 阶段 6：ML 与策略库（持续）

| 序号 | 事项 |
|------|------|
| 6.1 | 特征工程时间对齐检查（禁止用未来数据） |
| 6.2 | 模型持久化路径与版本号 |
| 6.3 | 除 RF 外可选 LightGBM/线性模型；默认仍保持依赖可选 |
| 6.4 | 策略注册表：`STRATEGY_REGISTRY`，Dashboard 与 CLI 统一发现 |
| 6.5 | 示例策略增加单元测试（信号在固定数据上的确定性） |

---

## 四、代码与设计层面的具体建议

### 4.1 核心引擎

- `EventEngine.put` 在 live 模式下改为线程安全  
- Handler 异常已 catch，建议增加 metrics 计数（失败次数、延迟）  
- 考虑 `EventType` 扩展：`TIMER`、`RISK_ALERT`，方便定时任务与风控熔断  

### 4.2 Portfolio / Risk

- 持仓对象暴露 `available_quantity`（T+1）  
- 回撤计算与 snapshot 频率文档化（按 bar 还是按日）  
- Sizer 接口统一：`(signal, portfolio, bar) -> qty`  

### 4.3 数据

- `Bar.frequency` 已有，feed 对齐多标的同时刻的逻辑需在文档与测试中固化  
- 合成数据增加「有趋势 / 均值回归 / 跳空」模式，方便策略回归测试  

### 4.4 安全与配置

- API Key、邮箱密码只走环境变量或本地 `*.yaml`（gitignore）  
- `notify.yaml.example` 已存在，CI 中检查无真实密钥提交  

### 4.5 性能

- 日线多标的回测目前足够；若全市场扫描，考虑向量化预计算指标 + 多进程按标的分片  
- 策略 bar 缓冲 `max_buffer` 已有，注意多策略多标的内存  

---

## 五、建议的里程碑与优先级看板

```text
Week 1–2   [P0] pyproject + pytest + CI + LICENSE + 安装路径修复
Week 2–3   [P0] README 双轨 + architecture 文档
Week 3–6   [P0/P1] A股 T+1/涨跌停/手数 + 成交量约束 + 核心测试
Week 6–8   [P1] 参数寻优 + Walk-Forward + 基准对比
Week 8–12  [P1] Paper 实时闭环 + 持仓/回测模型对齐
Week 12+   [P2] 一种实盘通道（Binance Testnet 或 CTP Simnow）
持续       [P2] 策略库扩展、Dashboard 统一、ML 严谨化
```

---

## 六、不建议急着做的事

- 同时对接多种实盘券商（骨架先保持，完成度 0→1 选一条）  
- 重写事件引擎为复杂 actor 模型（当前单线程足够回测；live 用队列即可）  
- 大而全的 Web 前后端（现有 Streamlit 对个人项目更划算）  
- 在无测试情况下大规模重构目录（先测试护栏再动刀）  

---

## 七、总结

项目在 **架构理念** 上已经对标成熟事件驱动框架（数据 → 信号 → 风控 → 订单 → 成交 → 组合），代码风格统一、注释质量不错，且叠加了很实用的 **A 股个人持仓与通知链路**。

当前最大的风险不是「缺功能」，而是 **工程化缺口**（测试、打包、CI、文档边界）会导致后续改规则/加策略时回归成本高。建议严格按 **基建 → 规则真实性 → Paper 闭环 → 单点实盘** 推进；把「框架」与「个人助手」在文档和入口上拆清，会大幅提升开源可读性与个人维护效率。

---

## 附录：可选后续交付物

1. `pyproject.toml` 初稿 + 最小 `tests/` 目录结构  
2. A 股 T+1 / 涨跌停 在 `SimulatedBroker` 中的接口设计草案  
3. GitHub Actions CI 工作流 YAML 模板  
