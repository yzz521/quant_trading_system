# GP Assistant (quant_trading_system)

[中文 README](README.md) | English

[![CI](https://github.com/yzz521/quant_trading_system/actions/workflows/ci.yml/badge.svg?branch=main-v3)](https://github.com/yzz521/quant_trading_system/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yzz521/quant_trading_system)](https://github.com/yzz521/quant_trading_system/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)](https://github.com/yzz521/quant_trading_system/releases/latest)

A daily research assistant for A-shares, Hong Kong and US stocks: full-market screening → sector rotation → 9-factor scoring → trading plan → look-ahead-safe backtest → AI explanation. It ships as a local dashboard, a daily email, and optional desktop builds.

> **Quant computes, AI explains, backtest verifies — you decide.**
> Prices, stops, targets and size come from the model. The LLM may only explain numbers that were already computed. The backtest checks the rules. This branch never sends a broker order.

> **Not investment advice.** For research and learning only. Scores, zones, position sizes and BUY/SELL labels are model output. You bear the risk of acting on them.

Default branch: `main-v3`. Older lines: `main-v2` (fuller V2) and `main` (the V1 event-driven framework; those modules are not in this branch). The app version is `APP_VERSION` in `utils/app_meta.py`, aligned with the [latest Release](https://github.com/yzz521/quant_trading_system/releases/latest). Changelog: [Releases](https://github.com/yzz521/quant_trading_system/releases).

**[Download the desktop app](https://github.com/yzz521/quant_trading_system/releases/latest)** (macOS / Windows / Linux; no local Python required).

---

## Screenshots

These images are placeholders, not the real UI. Replace them with output from `python examples/run_opportunity.py --synthetic`, or redact names. Do not commit real holdings.

| Today's opportunities | Plan and AI text | Holdings |
| --- | --- | --- |
| ![Opportunity placeholder](docs/images/opportunity.svg) | ![Plan placeholder](docs/images/plan-ai.svg) | ![Holdings placeholder](docs/images/holdings.svg) |

| Factor validation | Daily email |
| --- | --- |
| ![Factors placeholder](docs/images/factors.svg) | ![Email placeholder](docs/images/email.svg) |

`python examples/gen_email_preview.py` writes `results/email_cn_preview.html` (and `us` / `hk`) from sample data. A 15–30s demo GIF is not in the repo yet. If you add one, record with synthetic data: open the desktop app → set the amount you plan to invest → scan → open one plan's AI text → look at the backtest.

---

## What it does

| Feature | Description | Entry point |
|---|---|---|
| 🎯 **Today's plan** | Full-market screen (A-shares / HK / US) → sector rotation → 9-factor score → trading plan (entry zone, stop, three targets, risk/reward, size) → historical backtest → AI explanation. Size is scaled by a market regime from real index data. **No scan runs until you set the amount you plan to invest.** | Dashboard "今日机会", `examples/run_opportunity.py` |
| 💼 **Holdings** | SQLite holdings (add/edit/delete, weighted cost), profit and loss, sync from pasted trade confirmations, per-holding quant view (sell / trim / hold / may add) | Dashboard "持仓指挥台", `examples/my_holdings.py` |
| 🎯 **Sell / add references** | Sell zone, stop, staged path for deep losses, add-to-position reference | Holdings page, daily email |
| 📧 **Daily email** | A block is included only when that data exists: holdings, cash account, portfolio risk, holdings quant (once per trading day), today's opportunities, sell/add references | Dashboard "配置", `examples/run_scheduler.py` |
| 📊 **Factor validation** | IC/IR, quantile monotonicity, walk-forward. Verdicts adjust **composite** weights. A missing or stale report falls back to the baseline weights. | `dashboard/pages/3_factors.py`, `examples/validate_factors.py` |
| 👁 **Intraday watch** | Polls holdings / a watchlist and notifies on rule hits. **Off by default.** | Settings → 实时盯盘, `examples/run_realtime.py` |
| ⚙️ **Settings** | Email on/off, recipients, markets (CN / HK / US), scan parameters, optional Hithink key. Packaged builds can check for and install a newer GitHub release. | Dashboard "配置" |

Decision states: 🟢 `BUY_NOW` / 🟢 `BUY_ON_PULLBACK` / 🟡 `WATCH` / 🟠 `HOLD` / 🔴 `SELL` / ⛔ `AVOID`. The plan is `AVOID` when risk/reward is below 1.5, the price geometry is inconsistent, or the quality gate hard-rejects. No position size is computed in those cases.

---

## How a candidate gets picked

```
Full-market snapshot
   ↓ ① Hard filter: min turnover + price-change range + name exclusions + float cap
        one industry is capped at 25% of the pool by default, then the pool is filled
Top N (default 30, adjustable 5–80 in the dashboard)
   ↓ ② Sector rotation: Sina industry strength
        (60% price-change percentile + 40% turnover percentile)
   ↓ ③ 9-factor stock score (weights sum to 1.00)
        fundamental .12  growth .08  technical .20  momentum .05
        capital_flow .15  valuation .10  market_env .05  sector .05  risk .20
   ↓ ④ Opportunity score (0–100) + composite sort
        opportunity: price 20% · support 15% · trend 15% · distance to entry 15%
                     · risk/reward 20% · volatility 5% · similar pattern 10%
        composite default: quality 45% · timing 35% · payoff 20%
        (factor validation may down-weight these three, not the nine above)
   ↓ ⑤ Opportunity engine: support/resistance → entry → stop/targets → R/R → size → decision
🟢 Buy list (BUY_NOW / BUY_ON_PULLBACK)   🟡 Watch list (WATCH)
```

- **Screener** (`screener.py`): one snapshot, no K-lines at this step. A-shares default to turnover ≥ CNY 50 million and a float-cap floor of 20 (`float_cap_yi`, i.e. 2 billion yuan). Hong Kong defaults to turnover ≥ HKD 10 million (as the screener comment states) and a float-cap floor of 5 in the same snapshot unit. The default price-change window is −6% to +10%. Names containing `ST`, `退` or `*` are dropped. `N` / `C` are dropped only as a prefix, and only when the name contains Chinese characters (the new-listing mark), so names like `TCL科技` or Latin-only tickers are kept. US names come from the Nasdaq screener: last price &gt; USD 2, market cap ≥ USD 10 billion, and names matching ETF / ETN / Fund / Trust removed. If that call fails, the screener uses the configured pool plus a built-in list of well-known tickers. Universe sizes move with the market, so this README does not pin a count.
- **Sectors** (`sector.py`): strength and constituents both come from Sina industry endpoints (source comments say 49 industries). The constituent map is cached for 24 hours. Unmapped or failed lookups score a neutral 50 and do not stop the pipeline.
- **Nine factors** (`scoring/stock_score.py`): trend is the moving-average structure, confirmed by MACD/ADX. RSI/KDJ/CCI/WR are overbought filters, not their own factors. Candlestick patterns feed the similar-pattern slot (10%) of the opportunity score. Fibonacci retracements feed support/resistance and the entry anchor. News and announcements from the last 14 days fold into **risk (20%)**. A factor dimension with missing fundamentals is removed from the weighted total instead of being left in as a fake neutral 50.
- **Sort order**: the batch scanner defaults to the composite score (`sort_key="composite"` in `opportunity/batch_scanner.py`), not the opportunity score alone.
- **Quality gate** (`opportunity/quality_gate.py`): missing data is not a pass. Every failed rule is reported with a reason. Backtests can relax the data-coverage rules (historical financials are often unavailable) while keeping the score and risk/reward floors.

On the opportunity page, each market shows a buy list and a watch list as two sections, not as a nested pair of tabs. Markets themselves are separate tabs.

---

## Design principles

1. **The LLM never sets prices.** `stock_analysis/ai/guard.py` checks AI text before it is shown:
   - every number must map to a value in the computed plan (invented prices are blocked first),
   - the text must not contradict the decision (no buy language for `WATCH` / `AVOID`, no buy or hold language for `SELL`),
   - a fixed disclaimer is required whenever there is buy/sell language.

   If any check fails, a rule-based explanation is shown instead. With no AI config, the rule-based text is used as well.
2. **No look-ahead in backtests** (`backtest/trading_plan_backtest.py`): plans use only bars up to day T; outcomes use only bars after T. Indicators are causal. `OpportunityEngine` defaults to `fetch_news=False`, so today's announcements are not applied to historical bars.
3. **Fills that could actually happen** (`backtest/execution.py`): a limit buy fills only if price touches the order. Commissions, stamp duty (sell side), transfer fees and slippage are deducted. A gap through the stop fills at the open (the worse price); a gap through the target does the same. Limit-up / limit-down locks and trading halts do not fill. Daily volume participation is capped.
4. **Validation changes the composite weights only.** `research/factor_validation.py` measures IC/IR, quantile monotonicity and walk-forward stability. `scoring/factor_weights.py` turns the verdicts for `stock_score`, `opportunity_score` and `risk_reward_1` into multipliers on the three composite dimensions. Insufficient samples are not punished. A missing, broken, or stale report (older than `max_age_days`, default 30) falls back to quality 45% / timing 35% / payoff 20%. The nine stock-score weights above are not rewritten.
5. **Calibrated confidence** (`calibration.py`): Platt when the sample is small, isotonic once the sample clears the module's threshold, identity when there is not enough data. If a time order is provided, cross-validation is in time blocks so overlapping trades do not leak across folds. No calibrator is loaded by default, so rank and decisions stay as scored.
6. **Shadow mode before anything live** (`shadow_orders.py`): `BUY_NOW` / `BUY_ON_PULLBACK` become order intents, pass five gates (daily loss, single-name cap, total exposure, stale quotes, duplicate key of date + symbol + side), and are appended to `results/shadow_orders.jsonl`. The module has no send path and imports no broker client.

---

## Quick start

### 0. Desktop app (recommended)

Pushing a `v*` tag runs two workflows and uploads both to [Releases](https://github.com/yzz521/quant_trading_system/releases):

| Workflow | Artifacts | What it is |
|---|---|---|
| `build-app` | `GP-Assistant-macOS-arm64.zip`, `GP-Assistant-macOS-x64.zip`, `GP-Assistant-Windows.zip`, `GP-Assistant-Linux.tar.gz` | PyInstaller desktop app |
| `release-portable` | `quant_trading_system-portable-windows-x64.zip`, `…-macos-arm64.zip`, `…-macos-x64.zip`, `…-linux-x64.zip` | Portable bundle with a Python runtime |

Use a tag that does not already exist. `v0.3.12` is already published.

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

Set `APP_VERSION` in `utils/app_meta.py` to the same number (`0.3.12` → tag `v0.3.12`).

After downloading a desktop build:

- **Windows**: unzip the whole `GPAssistant` folder and run `GPAssistant.exe`. If SmartScreen appears, choose "More info" → "Run anyway". Don't copy the exe alone.
- **macOS** (no notarization): double-click `首次打开.command`, or run  
  `xattr -cr GP助手.app && codesign --force --deep --sign - GP助手.app && open GP助手.app`
- **Linux**: the archive contains the `GP助手` binary and needs `python3-gi` plus `gir1.2-webkit2-4.1`.

Build locally:

```bash
pip install -e ".[data,dashboard,gui]" pyinstaller
pyinstaller app/packaging/gp_assistant.spec --noconfirm
```

See [`app/README.md`](app/README.md).

The git tag `v1.0.0` is the old V1 line on `main`. It has **no GitHub Release**. Its number is higher than `v0.3.12`, and it is not the current app line. In-app update checks read the Releases API (`utils/app_meta.py`), not the highest git tag.

### 1. Install from source

```bash
git clone https://github.com/yzz521/quant_trading_system.git
cd quant_trading_system
git checkout main-v3

python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1

pip install -e ".[all]"            # data + dashboard + gui + dev
```

Dashboard and tests without the desktop shell (what CI installs): `pip install -e ".[dev,data,dashboard]"`.

### 2. Run

```bash
# No args: restart the scheduler, the dashboard, and the intraday watcher
./deploy/restart.sh
./deploy/restart.sh status

# Any platform
python deploy/ctl.py dashboard start
python deploy/ctl.py scheduler start
```

Dashboard: <http://localhost:8502> (home, today's opportunities, holdings, settings, factor validation). On macOS the scheduler prefers launchd (`com.gp.stock-scheduler`, if that job is bootstrapped); otherwise `restart.sh` falls back to `ctl.py`.

### 3. Tests and examples

```bash
pytest -q
ruff check stock_analysis dashboard utils examples

python examples/run_opportunity.py 600000 --account 100000
python examples/run_opportunity.py --synthetic
python examples/run_batch_opportunity.py 600000 000001 600519
python examples/run_backtest_plan.py 600000 --days 750
python examples/gen_email_preview.py
python examples/check_notify.py
```

`examples/` currently has 17 scripts. Besides the commands above: `validate_factors.py`, `fit_confidence_calibration.py`, `calibrate_weights.py`, `run_shadow.py`, `run_realtime.py`, `check_data_sources.py`, `my_holdings.py`, `run_scheduler.py`, and the `audit_*.py` / `calc_exposure_reduction.py` audits.

---

## Daily email

```bash
cp config/notify.yaml.example config/notify.yaml   # never commit real credentials
python examples/run_scheduler.py --test --market CN
python deploy/ctl.py scheduler start
```

The same switches, recipients and markets can be edited on the settings page. Channels: email (SMTP), ServerChan, Feishu. Log: `results/scheduler.log`.

`notifier.build_market_message` orders blocks as holdings → cash → portfolio risk → holdings quant → today's plans → sell/add references, plus a disclaimer. Empty inputs are omitted. The preview script fills holdings, holdings quant, today's plans and sell/add references only.

---

## Data sources

A-share quotes, valuation and financials use akshare (Sina / Tencent) by default. A Hithink (同花顺) Finance API key switches A-shares, including on-exchange ETFs, to that source. Hong Kong and US sources stay as they are. A missing key or a failed request falls back; auth failures trip a 5-minute circuit breaker.

Configure it on the settings page, in `config/hithink.env`, or with `HITHINK_FINANCE_API_KEY` (highest priority). Request a key at <https://fuyao.aicubes.cn/admin>. Packages ship `hithink.env.example` only.

Other sources: yfinance, Nasdaq screener.

---

## Project layout

```text
stock_analysis/
├── opportunity/   support/resistance, entry, exit, R/R, sizing, quality gate, plan, batch scanner
├── scoring/       9-factor score, opportunity score, composite, factor_weights
├── market/        regime from a real index, breadth, risk
├── backtest/      trading-plan backtest, execution/cost model, metrics
├── ai/            analyst + guard
├── research/      factor validation
├── screener.py / sector.py
├── holdings*.py, sell_zone.py, trade_monitor.py
├── calibration.py, shadow_orders.py, paper_tracking.py, portfolio_risk.py
├── notifier.py, scheduler.py, realtime.py, news.py, hithink.py, app_config.py
dashboard/         home.py + pages/0_opportunity, 1_holdings, 2_settings, 3_factors
app/               desktop shell (pywebview + PyInstaller)
deploy/            ctl.py, restart.sh, ctl.sh
examples/          17 runnable scripts
tests/             pytest suite (count it with pytest --collect-only; this file does not hard-code it)
utils/app_meta.py  APP_VERSION
```

---

## Security

Never commit `config/notify.yaml`, a real `holdings.db`, `config/hithink.env`, or SMTP / ServerChan / Feishu secrets (already in `.gitignore`).

`config/holdings.yaml` is the legacy holdings file. On first use, if the database is empty, it is imported into `holdings.db` beside it. `config/users.yaml` is the dashboard login file; if it is missing, the dashboard does not ask for a password.

## License

[MIT](LICENSE). For research and learning only — **not investment advice**.

Architecture: [`docs/architecture.md`](docs/architecture.md). Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
