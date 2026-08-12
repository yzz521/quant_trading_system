# quant_trading_system V2 开发计划

**从量化交易系统转向股票决策助手**

每天自动筛选值得关注的股票，并给出合理买入区间、止损价格、目标价格、风险收益比和建议仓位。

- **基线**：main
- **开发分支**：main-v2
- **核心**：Trading Plan
- **技术栈**：AI + Quant

---

## 01 · 项目重新定位

> **V2 核心目标：**  
> 每天自动从 A 股市场筛选值得关注的股票，并告诉用户：为什么关注、什么价格可以买、什么价格止损、什么价格卖、当前应该买还是等待。

### V2 不再以自动交易为核心

Broker、Paper Trading、Event Engine 等能力可以保留，但它们降级为底层基础设施，而不是项目核心。

**流程：**

全市场 → 候选股票 → 股票评分 → 交易机会 → 买卖价格 → Trading Plan → AI解释

---

## 02 · Git 分支策略

### main

当前 `main` 作为 V1 稳定基线，不再进行 V2 功能开发。

```
main
Commit:
05c732748a7f931179e23323de8be7ffda98d66d
```

### 创建 main-v2

```bash
git fetch origin

git checkout main
git pull --ff-only origin main

git checkout -b main-v2
git push -u origin main-v2
```

> **原则：**  
> main 是 V1 稳定版本，main-v2 是 V2 主开发分支。

---

## 03 · main 分支保护

GitHub → Settings → Branches → Branch protection。

- Require a pull request before merging
- Require approvals
- Require status checks
- Require conversation resolution
- 禁止 force push
- 禁止删除 main

```
main
 ↓
V1 稳定版本
 ↓
禁止直接开发

main-v2
 ↓
V2 主开发

feature/*
 ↓
V2 功能开发
```

---

## 04 · V2 核心架构

全市场 → Universe Filter → Funnel → Candidate Pool → Stock Score → Opportunity Score → Opportunity Engine → Trading Plan → AI Analyst

---

## 05 · 两套评分体系

### Stock Score

**回答：** 这只股票本身怎么样？

**分数范围：** 0–100

| 因素       | 权重 |
|------------|------|
| 基本面质量 | 20%  |
| 技术趋势   | 25%  |
| 资金面     | 15%  |
| 估值       | 10%  |
| 市场环境   | 10%  |
| 风险       | 20%  |

### Opportunity Score

**回答：** 现在这个价格值不值得交易？

**分数范围：** 0–100

| 因素           | 权重 |
|----------------|------|
| 当前价格位置   | 20%  |
| 支撑强度       | 15%  |
| 趋势状态       | 15%  |
| 买入距离       | 15%  |
| 风险收益比     | 20%  |
| 波动率         | 5%   |
| 历史类似走势   | 10%  |

> **关键区别：**  
> 好股票不一定等于现在可以买。  
> V2 必须同时判断“股票质量”和“当前交易机会”。

---

## 06 · Opportunity Engine

新增核心目录：

```
stock_analysis/opportunity/
├── opportunity_engine.py
├── entry_price.py
├── exit_price.py
├── support_resistance.py
├── risk_reward.py
├── position_sizing.py
└── trading_plan.py
```

这是 V2 最核心的新模块。

---

## 07 · Entry Price

买入价不能简单使用固定比例计算。

**输入：**

- 当前价格
- MA5 / MA20 / MA60
- 前高 / 前低
- 支撑位 / 压力位
- ATR
- Bollinger
- 成交量
- 突破位置
- 成交密集区

**示例：**

```
当前价格：12.36

理想买入：11.80
标准买入：11.95
激进买入：12.10

买入区间：
11.80 ~ 12.10
```

---

## 08 · Stop Loss

**止损来源：**

- 结构止损
- ATR 止损
- 支撑位止损
- 固定风险止损

**示例：**

```
买入：11.95

关键支撑：11.42
ATR 止损：11.38

最终止损：11.35
```

---

## 09 · Target Price

至少支持三个目标价：

```
买入：11.95
止损：11.35

目标1：13.20
目标2：14.50
目标3：15.80
```

同时计算：

```
expected_return
risk_reward
max_loss
```

---

## 10 · Trading Plan

```json
{
  "code": "...",
  "name": "...",

  "decision": "BUY_ON_PULLBACK",

  "stock_score": 91,
  "opportunity_score": 94,

  "current_price": 12.36,

  "entry_low": 11.80,
  "entry_price": 11.95,
  "entry_high": 12.10,

  "stop_loss": 11.35,

  "target_1": 13.20,
  "target_2": 14.50,
  "target_3": 15.80,

  "risk_reward_1": 2.08,
  "risk_reward_2": 4.25,

  "position_percent": 20,

  "holding_period": "5~20 trading days",

  "confidence": 0.87
}
```

---

## 11 · 决策状态

| 状态 | 说明 |
|------|------|
| 🟢 **BUY_NOW** | 当前价格已经进入合理买入区域。 |
| 🟢 **BUY_ON_PULLBACK** | 股票不错，但当前价格偏高，等待回调。 |
| 🟡 **WATCH** | 值得观察，但交易条件尚未成立。 |
| 🟠 **HOLD** | 已经持有，继续观察持仓逻辑。 |
| 🔴 **SELL** | 达到目标位或交易逻辑失效。 |
| ⛔ **AVOID** | 风险过高，不进入交易候选池。 |

---

## 12 · Funnel 定位

现有 Funnel 不删除，但定位调整为：**Candidate Generator**

```
L1 流动性
   ↓
L2 基本质量
   ↓
L3 技术面
   ↓
L4 资金 + 新闻风险
   ↓
Candidate Pool
```

Funnel 负责筛选候选股票，不负责最终买卖决策。

---

## 13 · Risk / Reward

```
买入：11.95
止损：11.35
目标：13.20

风险 = 11.95 - 11.35
收益 = 13.20 - 11.95
```

| RR        | 判断   |
|-----------|--------|
| < 1.5     | 不推荐 |
| 1.5 ~ 2.0 | 可观察 |
| 2.0 ~ 3.0 | 较好   |
| > 3.0     | 优秀   |

---

## 14 · 仓位建议

用户输入账户资产，例如：

```
账户：10,000 元
```

系统计算：

```
最大允许亏损
      ↓
每股最大风险
      ↓
最大股数
      ↓
建议仓位
```

> 单只股票不能无限加仓。  
> 仓位必须与止损距离和账户风险绑定。

---

## 15 · 市场环境

```
stock_analysis/market/
├── market_regime.py
├── market_breadth.py
└── market_risk.py
```

**市场状态：**

- BULL
- NEUTRAL
- BEAR
- HIGH_RISK

市场环境影响 Opportunity Score 和 Position Size。

---

## 16 · AI 的正确定位

> **AI 不负责直接决定买卖价格。**

**流程：**

行情数据 → 量化计算 → Trading Plan → AI → 自然语言解释

**AI 主要负责：**

- 为什么值得关注
- 为什么现在可以买 / 不能买
- 买入逻辑
- 主要风险
- 失效条件
- 已经持有时怎么办

---

## 17 · 历史回测

V2 必须回答：**这套买入、止损、目标价规则历史上是否有效？**

| 指标           | 说明                 |
|----------------|----------------------|
| 样本数量       | 历史交易计划数量     |
| 买入区间命中率 | 价格进入 Entry Zone 的比例 |
| 止损触发率     | 交易逻辑失败比例     |
| 目标1触达率    | 达到第一目标比例     |
| 目标2触达率    | 达到第二目标比例     |
| 胜率           | 盈利交易比例         |
| 平均收益       | 平均交易收益         |
| 最大回撤       | 策略最大历史回撤     |
| 平均持仓       | 平均持仓交易日       |

> **硬性要求：**  
> 严格防止未来函数。  
> 不能使用未来价格、未来财务数据、未来新闻等信息。

---

## 18 · 每日运行流程

① 全市场行情 → ② 基础过滤 → ③ Funnel → ④ Top 50~100 → ⑤ Stock Score → ⑥ Opportunity Score

⑦ Entry → ⑧ Stop → ⑨ Target → ⑩ RR → ⑪ Position → ⑫ Trading Plan → ⑬ AI

---

## 19 · V2 项目结构

```
quant_trading_system/
│
├── stock_analysis/
│   ├── data/
│   ├── indicators/
│   ├── patterns/
│   ├── scanner/
│   ├── funnel/
│   │
│   ├── scoring/
│   │   ├── stock_score.py
│   │   ├── opportunity_score.py
│   │   └── score_components.py
│   │
│   ├── opportunity/
│   │   ├── opportunity_engine.py
│   │   ├── entry_price.py
│   │   ├── exit_price.py
│   │   ├── support_resistance.py
│   │   ├── risk_reward.py
│   │   ├── position_sizing.py
│   │   └── trading_plan.py
│   │
│   ├── market/
│   ├── backtest/
│   ├── ai/
│   └── portfolio/
│
├── dashboard/
├── tests/
└── docs/
```

---

## 20 · 开发阶段

| 阶段    | 标题             | 内容                                       |
|---------|------------------|--------------------------------------------|
| Phase 0 | V2 基线          | 建立 main-v2、锁定 main、建立测试基线      |
| Phase 1 | 评分体系         | Stock Score + Opportunity Score            |
| Phase 2 | 买入价格         | 支撑压力 + Entry Zone                      |
| Phase 3 | 止损 + 目标价    | Stop Loss + Target Price + Risk/Reward     |
| Phase 4 | Trading Plan     | 统一交易计划和 BUY/WATCH/SELL 状态         |
| Phase 5 | 仓位管理         | 账户风险 + 最大亏损 + 建议股数             |
| Phase 6 | 历史回测         | 验证买卖规则是否有效                       |
| Phase 7 | AI Analyst       | AI 解释量化结果                            |
| Phase 8 | Dashboard V2     | 围绕每日投资决策重新设计首页               |

---

## 21 · 开发优先级

```
P0  Trading Plan 核心

P1  选股 + 买入 + 止损 + 目标价

P2  历史回测

P3  AI 分析

P4  Dashboard

P5  Paper Trading

P6  自动交易
```

> 在 Trading Plan 没有经过历史验证之前，不继续扩展自动交易能力。

---

## 22 · 第一批实际开发任务

- [x] 建立 V2 分支和保护规则
- [x] 建立 V2 开发文档
- [x] 梳理现有 scanner / funnel / diagnosis / sell_zone
- [x] 建立 Stock Score
- [x] 建立 Opportunity Score
- [x] 建立 Support / Resistance Engine
- [x] 建立 Entry Price Engine
- [x] 建立 Stop Loss Engine
- [x] 建立 Target Price Engine
- [x] 建立 Risk / Reward Engine
- [x] 建立 TradingPlan
- [x] 建立 Position Sizing
- [x] 建立 Trading Plan Backtest
- [x] 接入 AI Analyst
- [x] 重做 Dashboard

---

## 23 · Definition of Done

- [x] 可以扫描全市场
- [x] 可以得到候选股票 Top N
- [x] 每只股票有 Stock Score
- [x] 每只股票有 Opportunity Score
- [x] 每只股票有买入区间
- [x] 每只股票有止损
- [x] 每只股票至少有两个目标价
- [x] 有风险收益比
- [x] 有建议仓位
- [x] 有 BUY_NOW / BUY_ON_PULLBACK / WATCH 等状态
- [x] 有历史回测
- [x] 无未来函数
- [x] AI 可以解释结果
- [x] Dashboard 可以直接查看每日机会
- [x] 原有 main 不被破坏

---

## 24 · 最重要的产品原则

> **量化模型负责计算，AI 负责解释，回测负责验证，系统负责整理，用户负责最终决策。**

V2 不追求“自动帮用户炒股”，而是追求：  
**让用户每天知道应该看什么、为什么看、什么价格买、买多少、什么时候止损、什么时候卖。**

---

## 25 · 最终产品形态

```
              今日市场

市场状态：🟢 偏多

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 今日重点

① XXX

Stock Score        92
Opportunity Score  94

当前价格           12.36
买入区间           11.80 ~ 12.10
止损               11.35
目标1              13.20
目标2              14.50

风险收益比         1 : 2.08

建议：
🟢 BUY_ON_PULLBACK

理由：
股票趋势良好，但当前价格略高，
等待回调至买入区间。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

② XXX
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

我的持仓

XXX    +8.2%    🟢 HOLD
XXX    -2.1%    🟡 WATCH
XXX   +16.4%    🔴 SELL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 26 · V2 最终定位

> **股票筛选 + 买卖价格 + 风险收益比 + 仓位建议 + AI 解释的个人投资决策系统**

最终不是告诉用户：  
“RSI 是 63，MACD 金叉，布林带中轨……”

而是告诉用户：  
**“这只股票值得关注；现在不要追高；如果回调到 11.80~12.10，可以考虑；11.35 跌破后交易逻辑失效；第一目标 13.20，第二目标 14.50。”**

---

## 27 · 落地状态（2026-08-12）

P0–P4 已全部实现并完成真实数据全链路验证（`main-v2` 分支，全套 193 项测试通过）：

- **评分系统**：Stock Score（6 维加权）+ Opportunity Score（7 维加权）
- **机会引擎**：支撑阻力 / 入场区间（理想/标准/激进）/ 止损（多源取最严）/ 三档目标 / 风险收益比 / 仓位（账户风险 × 止损距离）
- **TradingPlan**：BUY_NOW / BUY_ON_PULLBACK / WATCH / HOLD / SELL / AVOID（RR<1.5 即 AVOID，不计算仓位）
- **历史回测**：逐日滑动回测，严格防 look-ahead（计划只用截至 T 日数据生成）
- **AI 分析师**：量化结果 → 自然语言（AI 只解释、不定价），无 key 时规则化兜底
- **市场环境**：真实指数（上证/沪深300）→ Regime / 风险，失败降级中性
- **批量机会扫描**：候选股 → 并发逐个生成计划，可接入每日邮件「今日机会」区块
- **Dashboard V2**：市场状态卡片 + 交易计划 + AI 解读 + 回测 + 批量扫描

**剩余（后续里程碑，按计划书原则等回测充分验证后再做）**：P5 模拟盘、P6 自动交易。

---

*quant_trading_system · V2 Development Plan · 2026*
