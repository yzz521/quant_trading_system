# quant_trading_system V2 Development Plan

**From Quantitative Trading System to Stock Decision Assistant**

Automatically screen stocks worth watching every day, and provide reasonable entry zones, stop-loss prices, target prices, risk-reward ratios, and suggested position sizes.

- **Baseline**: main
- **Development Branch**: main-v2
- **Core**: Trading Plan
- **Tech Stack**: AI + Quant

---

## 01 · Project Repositioning

> **V2 Core Goal:**  
> Automatically screen stocks worth watching from the A-share market every day, and tell the user: why to watch, at what price to buy, at what price to stop loss, at what price to sell, and whether to buy now or wait.

### V2 No Longer Focuses on Automated Trading as the Core

Capabilities such as Broker, Paper Trading, and Event Engine can be retained, but they are demoted to underlying infrastructure rather than the project core.

**Flow:**

Full Market → Candidate Stocks → Stock Scoring → Trading Opportunity → Entry/Exit Prices → Trading Plan → AI Explanation

---

## 02 · Git Branch Strategy

### main

The current `main` serves as the stable V1 baseline and will no longer receive V2 feature development.

```
main
Commit:
05c732748a7f931179e23323de8be7ffda98d66d
```

### Create main-v2

```bash
git fetch origin

git checkout main
git pull --ff-only origin main

git checkout -b main-v2
git push -u origin main-v2
```

> **Principle:**  
> main is the stable V1 version; main-v2 is the main development branch for V2.

---

## 03 · main Branch Protection

GitHub → Settings → Branches → Branch protection.

- Require a pull request before merging
- Require approvals
- Require status checks
- Require conversation resolution
- Prohibit force push
- Prohibit deletion of main

```
main
 ↓
V1 Stable Version
 ↓
No Direct Development

main-v2
 ↓
V2 Main Development

feature/*
 ↓
V2 Feature Development
```

---

## 04 · V2 Core Architecture

Full Market → Universe Filter → Funnel → Candidate Pool → Stock Score → Opportunity Score → Opportunity Engine → Trading Plan → AI Analyst

---

## 05 · Two Scoring Systems

### Stock Score

**Answers:** How good is this stock itself?

**Score Range:** 0–100

| Factor              | Weight |
|---------------------|--------|
| Fundamental Quality | 20%    |
| Technical Trend     | 25%    |
| Capital Flow        | 15%    |
| Valuation           | 10%    |
| Market Environment  | 10%    |
| Risk                | 20%    |

### Opportunity Score

**Answers:** Is the current price worth trading?

**Score Range:** 0–100

| Factor                    | Weight |
|---------------------------|--------|
| Current Price Position    | 20%    |
| Support Strength          | 15%    |
| Trend Status              | 15%    |
| Distance to Entry         | 15%    |
| Risk-Reward Ratio         | 20%    |
| Volatility                | 5%     |
| Historical Similar Patterns | 10%  |

> **Key Distinction:**  
> A good stock does not necessarily mean it is buyable right now.  
> V2 must evaluate both “stock quality” and “current trading opportunity.”

---

## 06 · Opportunity Engine

New core directory:

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

This is the most critical new module in V2.

---

## 07 · Entry Price

Entry price cannot be calculated using a simple fixed percentage.

**Inputs:**

- Current price
- MA5 / MA20 / MA60
- Previous high / Previous low
- Support / Resistance levels
- ATR
- Bollinger Bands
- Volume
- Breakout levels
- Volume congestion zones

**Example:**

```
Current Price: 12.36

Ideal Entry:   11.80
Standard Entry: 11.95
Aggressive Entry: 12.10

Entry Zone:
11.80 ~ 12.10
```

---

## 08 · Stop Loss

**Stop-loss sources:**

- Structural stop
- ATR stop
- Support-level stop
- Fixed-risk stop

**Example:**

```
Entry: 11.95

Key Support: 11.42
ATR Stop:    11.38

Final Stop Loss: 11.35
```

---

## 09 · Target Price

At least three target prices must be supported:

```
Entry: 11.95
Stop:  11.35

Target 1: 13.20
Target 2: 14.50
Target 3: 15.80
```

Also calculate:

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

## 11 · Decision States

| State | Description |
|-------|-------------|
| 🟢 **BUY_NOW** | Current price has already entered a reasonable entry zone. |
| 🟢 **BUY_ON_PULLBACK** | The stock is good, but the current price is elevated; wait for a pullback. |
| 🟡 **WATCH** | Worth monitoring, but trading conditions are not yet met. |
| 🟠 **HOLD** | Already held; continue monitoring the holding logic. |
| 🔴 **SELL** | Target reached or trading thesis invalidated. |
| ⛔ **AVOID** | Risk is too high; do not enter the candidate pool. |

---

## 12 · Funnel Positioning

The existing Funnel is not removed, but its role is redefined as: **Candidate Generator**

```
L1 Liquidity
   ↓
L2 Basic Quality
   ↓
L3 Technicals
   ↓
L4 Capital + News Risk
   ↓
Candidate Pool
```

Funnel is responsible for screening candidate stocks; it does not make final buy/sell decisions.

---

## 13 · Risk / Reward

```
Entry:  11.95
Stop:   11.35
Target: 13.20

Risk   = 11.95 - 11.35
Reward = 13.20 - 11.95
```

| RR        | Judgment   |
|-----------|------------|
| < 1.5     | Not recommended |
| 1.5 ~ 2.0 | Observable |
| 2.0 ~ 3.0 | Good       |
| > 3.0     | Excellent  |

---

## 14 · Position Sizing

User inputs account equity, for example:

```
Account: 10,000 CNY
```

System calculates:

```
Maximum Allowed Loss
      ↓
Maximum Risk per Share
      ↓
Maximum Shares
      ↓
Suggested Position
```

> A single stock cannot be scaled indefinitely.  
> Position size must be bound to stop-loss distance and account risk.

---

## 15 · Market Environment

```
stock_analysis/market/
├── market_regime.py
├── market_breadth.py
└── market_risk.py
```

**Market Regimes:**

- BULL
- NEUTRAL
- BEAR
- HIGH_RISK

Market regime affects Opportunity Score and Position Size.

---

## 16 · Correct Positioning of AI

> **AI is not responsible for directly deciding buy/sell prices.**

**Flow:**

Market Data → Quantitative Calculation → Trading Plan → AI → Natural Language Explanation

**AI is primarily responsible for:**

- Why the stock is worth watching
- Why it can / cannot be bought now
- Entry logic
- Key risks
- Invalidation conditions
- What to do if already held

---

## 17 · Historical Backtesting

V2 must answer: **Have these entry, stop-loss, and target-price rules been effective historically?**

| Metric                      | Description                              |
|-----------------------------|------------------------------------------|
| Sample Size                 | Number of historical trading plans       |
| Entry Zone Hit Rate         | Proportion of prices entering Entry Zone |
| Stop-Loss Trigger Rate      | Proportion of failed trading theses      |
| Target 1 Hit Rate           | Proportion reaching first target         |
| Target 2 Hit Rate           | Proportion reaching second target        |
| Win Rate                    | Proportion of profitable trades          |
| Average Return              | Average trade return                     |
| Maximum Drawdown            | Maximum historical drawdown of the strategy |
| Average Holding Period      | Average holding days                     |

> **Hard Requirement:**  
> Strictly prevent look-ahead bias.  
> Do not use future prices, future financial data, future news, or any other forward-looking information.

---

## 18 · Daily Runtime Flow

① Full Market Data → ② Basic Filtering → ③ Funnel → ④ Top 50~100 → ⑤ Stock Score → ⑥ Opportunity Score

⑦ Entry → ⑧ Stop → ⑨ Target → ⑩ RR → ⑪ Position → ⑫ Trading Plan → ⑬ AI

---

## 19 · V2 Project Structure

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

## 20 · Development Phases

| Phase   | Title              | Content                                              |
|---------|--------------------|------------------------------------------------------|
| Phase 0 | V2 Baseline        | Create main-v2, lock main, establish test baseline   |
| Phase 1 | Scoring System     | Stock Score + Opportunity Score                      |
| Phase 2 | Entry Price        | Support/Resistance + Entry Zone                      |
| Phase 3 | Stop + Target      | Stop Loss + Target Price + Risk/Reward               |
| Phase 4 | Trading Plan       | Unified trading plan and BUY/WATCH/SELL states       |
| Phase 5 | Position Sizing    | Account risk + max loss + suggested shares           |
| Phase 6 | Historical Backtest| Validate whether entry/exit rules are effective      |
| Phase 7 | AI Analyst         | AI explains quantitative results                     |
| Phase 8 | Dashboard V2       | Redesign homepage around daily investment decisions  |

---

## 21 · Development Priority

```
P0  Trading Plan Core

P1  Stock Selection + Entry + Stop Loss + Target Price

P2  Historical Backtesting

P3  AI Analysis

P4  Dashboard

P5  Paper Trading

P6  Automated Trading
```

> Do not expand automated trading capabilities until the Trading Plan has been historically validated.

---

## 22 · First Batch of Concrete Development Tasks

- [x] Establish V2 branch and protection rules
- [x] Establish V2 development documentation
- [x] Review existing scanner / funnel / diagnosis / sell_zone
- [x] Build Stock Score
- [x] Build Opportunity Score
- [x] Build Support / Resistance Engine
- [x] Build Entry Price Engine
- [x] Build Stop Loss Engine
- [x] Build Target Price Engine
- [x] Build Risk / Reward Engine
- [x] Build TradingPlan
- [x] Build Position Sizing
- [x] Build Trading Plan Backtest
- [x] Integrate AI Analyst
- [x] Rebuild Dashboard

---

## 23 · Definition of Done

- [x] Can scan the full market
- [x] Can obtain Top N candidate stocks
- [x] Each stock has a Stock Score
- [x] Each stock has an Opportunity Score
- [x] Each stock has an entry zone
- [x] Each stock has a stop loss
- [x] Each stock has at least two target prices
- [x] Has risk-reward ratio
- [x] Has suggested position size
- [x] Has BUY_NOW / BUY_ON_PULLBACK / WATCH and other states
- [x] Has historical backtesting
- [x] No look-ahead bias
- [x] AI can explain the results
- [x] Dashboard can directly view daily opportunities
- [x] Original main is not broken

---

## 24 · Most Important Product Principle

> **Quantitative models are responsible for calculation, AI for explanation, backtesting for validation, the system for organization, and the user for the final decision.**

V2 does not aim to “automatically trade stocks for the user.” Instead, it aims to:  
**Let the user know every day what to watch, why to watch it, at what price to buy, how much to buy, when to stop loss, and when to sell.**

---

## 25 · Final Product Form

```
              Today's Market

Market Regime: 🟢 Bullish Bias

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 Today's Highlights

① XXX

Stock Score        92
Opportunity Score  94

Current Price      12.36
Entry Zone         11.80 ~ 12.10
Stop Loss          11.35
Target 1           13.20
Target 2           14.50

Risk-Reward        1 : 2.08

Recommendation:
🟢 BUY_ON_PULLBACK

Reason:
The stock has a solid trend, but the current price is slightly elevated.
Wait for a pullback into the entry zone.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

② XXX
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

My Positions

XXX    +8.2%    🟢 HOLD
XXX    -2.1%    🟡 WATCH
XXX   +16.4%    🔴 SELL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 26 · V2 Final Positioning

> **A personal investment decision system combining stock screening + entry/exit prices + risk-reward ratio + position sizing + AI explanation**

Ultimately, it does not tell the user:  
“RSI is 63, MACD golden cross, Bollinger middle band…”

Instead, it tells the user:  
**“This stock is worth watching; do not chase the high now; if it pulls back to 11.80~12.10, consider it; if 11.35 is broken, the trading thesis is invalidated; first target 13.20, second target 14.50.”**

---

## 27 · Implementation Status (2026-08-12)

P0–P4 are fully implemented and validated with real data end-to-end (`main-v2` branch, 193 tests passing):

- **Scoring**: Stock Score (6-dim weighted) + Opportunity Score (7-dim weighted)
- **Opportunity Engine**: support/resistance, entry zone (ideal/standard/aggressive), stop loss (strictest of multiple sources), 3 targets, risk-reward, position sizing (account risk × stop distance)
- **TradingPlan**: BUY_NOW / BUY_ON_PULLBACK / WATCH / HOLD / SELL / AVOID (RR < 1.5 → AVOID, no position)
- **Backtest**: walk-forward daily simulation, strictly no look-ahead (plans built only from data up to T)
- **AI Analyst**: quant results → natural language (AI explains, never prices); rule-based fallback without key
- **Market**: real index (SSE/CSI300) → Regime / Risk; degrades to neutral on failure
- **Batch Scanner**: candidates → concurrent per-stock plans, feeds the "Today's Opportunities" email block
- **Dashboard V2**: market cards + trading plan + AI + backtest + batch scan

**Remaining (later milestones, after backtesting is sufficiently validated)**: P5 Paper Trading, P6 Automated Trading.

---

*quant_trading_system · V2 Development Plan · 2026*
