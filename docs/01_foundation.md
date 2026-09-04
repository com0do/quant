# I. Foundations and Principles

> This file is a must-read for getting started. It merges 4 foundational documents: the system evolution plan, trading notes, risk-control rules, and the advantages and limitations of quant.
> If you only have 1 hour, read this one first.

## Table of Contents

- [1. System Evolution Plan (Architecture + Contradictions + ADRs + Roadmap)](#1-system-evolution-plan)
- [2. User Trading Notes (Trading Insights and Live-Trading Rules)](#2-user-trading-notes)
- [3. Live Execution and Risk-Control Rules (Non-Negotiable Iron Rules)](#3-live-execution-and-risk-control-rules)
- [4. Advantages and Limitations of Quant (4 Pairs of Reflections)](#4-advantages-and-limitations-of-quant)
- [5. Strategy Lifecycle: Freshness, Decay, Elegy and Evolution](#5-strategy-lifecycle-freshness-decay-elegy-and-evolution)

---

<a id="1-system-evolution-plan"></a>
# Quant System Evolution Plan

> Based on an in-depth review of the current architecture + a survey of external dependencies, this identifies the core contradictions and gives a phased evolution roadmap.

---

## I. Current Architecture Summary

```
┌── Data layer ────────────────────────────────────────┐
│  JoinQuant ──jqdatasdk──▶ SQLite (one DB per index)  │
│  scripts/jq_bulk_sync_smallcap.py                    │
│  scripts/run_tomorrow_sync.sh                        │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌── Analysis layer ────────────────────────────────────┐
│  quant/backtest/engine.py    Backtest engine         │
│  quant/stock_strategy/       9 strategies (1 live)   │
│  quant/tools/optimize.py     Grid search + fwd test  │
│  quant/tools/vectorbt_optimize.py Momentum prefilter │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌── Execution layer ───────────────────────────────────┐
│  main.py --mode live-auto                            │
│  quant/live/daemon.py        Live-trading daemon     │
│  quant/execution/qmt_http_broker.py  ──HTTP──▶       │
│                                                     │
│  ┌──── Windows 11 ──────────────────────────┐       │
│  │  xtquant_gateway/ (FastAPI)              │       │
│  │       │                                   │       │
│  │  MiniQMT (xtquant.xttrader)              │       │
│  └──────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────┘
```

**Current assessment**: The architecture is complete, modular and well documented. The core risks are external dependencies (MiniQMT being phased out, JQ quota) and the ability to evolve strategies.

---

## II. Core Contradictions and Trade-offs

### Contradiction 1: GitHub Actions Scheduled Triggers vs Windows QMT Execution

| Dimension | Analysis |
|------|------|
| **Nature of the contradiction** | The TODO expects GitHub Actions to drive trading on a schedule, but QMT/MiniQMT are Windows GUI programs and are unsuitable for a CI environment |
| **Why QMT cannot run on GitHub Actions** | 1) Broker account credentials must not be put in CI; 2) QMT login requires GUI interaction; 3) The network environment (broker VPN/dedicated line) is not controllable; 4) Security and compliance risk |
| **Trade-off conclusion** | **GitHub Actions ≠ an execution environment. It is an orchestration layer.** |

**Recommended division of labor**:

```
GitHub Actions (cloud)              Local machine (Windows/Linux)
┌──────────────────────┐            ┌──────────────────────────┐
│ Schedule trigger     │            │                          │
│ JQ data sync (EOD)   │            │ Intraday quotes (QMT)    │
│ Backtest drift check │            │ Live trade execution     │
│ Factor + signal gen  │            │ Risk circuit breaker     │
│ Auto param tuning    │            │ End-of-day report        │
│ Anomaly alerts       │            │                          │
└──────┬───────────────┘            └──────────────────────────┘
       │                                      ▲
       │  git push signals/params             │
       └──────────────────────────────────────┘
              git pull (on the local machine)
```

### Contradiction 2: Long-Term Viability of MiniQMT

| Dimension | Analysis |
|------|------|
| **Current status** | Guojin Securities still supports MiniQMT today, with a low entry barrier |
| **Risk** | At least one broker has announced it will stop accepting new MiniQMT applications in 2026, and existing users will also be phased out gradually afterwards |
| **Good news** | The XtQuant API is **compatible** between MiniQMT and QMT — the full-featured QMT edition likewise provides `xtdata` + `xttrader` |
| **Trade-off conclusion** | **Keep the current MiniQMT gateway, but have a QMT fallback path ready.** |

**Migration path (if needed)**:

```
Now:  Linux ──HTTP──▶ MiniQMT (Windows)
Next: Linux ──HTTP──▶ QMT API Server (Windows)
                      │
                      └── The full-featured QMT edition also supports the xtquant Python API
                          only the connection method of xtquant_gateway needs adjusting
```

**Recommendation**: add support in `xtquant_gateway` for connecting to the full-featured QMT edition (non-lite mode) as a fallback. The `xtquant` API is identical in both modes; the only difference is in the login/initialization step.

### Contradiction 3: Simple Strategies vs Complex Strategies (Overfitting)

| Dimension | Analysis |
|------|------|
| **Nature of the contradiction** | Over-optimizing on historical data → overfitting → failure in live trading. But simple strategies are not good enough either. |
| **Current approach** | 9 strategies (including the newly added `gbm_multifactor` MVP), with only 1 used in production (`csi1000_enhanced`); a regime switch provides adaptation |
| **Problem** | The regime switch is a static rule (`breadth > 0.60 → risk_on`) and does not evolve as market structure changes |

**Trade-off conclusion: strategies must be simple, validation must be strict, and adaptation must be online.**

```
Simple strategy + strict validation + online fine-tuning
   ▲                  ▲                  ▲
   │                  │                  └── Online learning (parameters drift slowly)
   │                  └── walk-forward validation (not a static backtest)
   └── Strategy logic ≤3 core factors
```

| Principle | Practice |
|------|---------|
| **Simple strategies** | At most 3 core factors per strategy, with explainable logic |
| **Strict validation** | Must pass walk-forward (rolling-window) validation; do not rely on full-sample backtests alone |
| **Online fine-tuning** | Use online learning (e.g. exponentiated gradient) to let parameters drift slowly with the market |
| **Ensemble, not stacking** | An ensemble of several simple strategies beats one complex strategy |
| **Periodic retirement** | Every strategy has a lifecycle; evaluate periodically whether it has stopped working, and retire it if so |

### Contradiction 4: Local Data vs Multi-Machine Sync

| Dimension | Analysis |
|------|------|
| **Nature of the contradiction** | SQLite databases (hundreds of MB to several GB) are not suitable for git, but work must happen on multiple computers |
| **Trade-off conclusion** | **Sync code with git, sync data with scripts.** |

```
Git-managed (lightweight)        Script-managed (heavyweight)
┌─────────────────┐              ┌─────────────────────┐
│ *.py             │              │ data/runtime/*.db    │
│ *.toml           │              │ data/archive/*.db    │
│ *.sh             │              │ data/meta/*.db       │
│ docs/*.md        │              │ output/*.csv         │
│ .gitignore       │              │ *.lock               │
└─────────────────┘              └─────────────────────┘
```

**Data initialization on each machine**: run `scripts/jq_bulk_sync_smallcap.py` once to pull the full dataset from JQ, then sync incrementally every day.

### Contradiction 5: When to Introduce ML/AI Agents

| Dimension | Analysis |
|------|------|
| **TODO request** | "Use machine learning and AI agents (hermes) for data analysis, financial-report analysis, and a data feedback loop" |
| **Current state** | No ML; the backtest engine uses purely hand-crafted technical features (MA, MACD, candlestick patterns, etc.) |
| **Wrong path** | Directly replace the existing strategies with ML models → black-boxing → unexplainable → uncontrollable risk |
| **Right path** | Use ML for auxiliary analysis (feature discovery, anomaly detection) and keep the strategy logic transparent |

**Reasonable entry points for ML/AI** (by priority):

1. **Anomaly detection** (low risk, high value): use ML to detect market anomalies (e.g. flash-crash warnings, abnormal volume spikes) as risk-control signals
2. **Factor discovery** (medium risk): use ML to mine new factors from financial reports / price-volume data, and add them to the strategy pool after walk-forward validation
3. **Parameter adaptation** (medium risk): use online learning to fine-tune strategy parameters (rather than replace the strategy logic)
4. **NLP financial-report analysis** (low risk): use an AI agent to read financial-report text and extract structured signals (e.g. management tone, risk warnings)

---

## III. Evolution Roadmap

### Phase 0: Harden the Current Architecture (Highest Priority, 1-2 Weeks)

**Goal**: get the existing system running stably while MiniQMT is still available, and accumulate live-trading experience.

| # | Task | Description |
|---|------|------|
| 0.1 | **Complete MiniQMT gateway integration testing** | Make sure `live_qmt_http.toml` + `xtquant_gateway/` + Windows MiniQMT run end to end |
| 0.2 | **Establish a daily run rhythm** | `run_daily_live_pipeline.sh` runs stably locally (pre-market sync → backtest → scan → live trading) |
| 0.3 | **Paper trading first** | Run paper trading with `dry_run = true` for 1-2 weeks first to validate signal quality |
| 0.4 | **Complete .gitignore** | Make sure `data/runtime/*.db`, `data/archive/*.db`, `output/`, and `.env` never enter git |
| 0.5 | **Verify cross-machine sync** | On a second computer, `git clone` + `jq_bulk_sync`, and confirm the full workflow runs |

### Phase 1: Introduce GitHub Actions (2-4 Weeks)

**Goal**: have GitHub Actions handle post-close data sync + backtest validation, with the local machine doing execution only.

| # | Task | Description |
|---|------|------|
| 1.1 | **Create `.github/workflows/daily_sync.yml`** | Triggered at 15:30 (after the close) every trading day: incremental JQ data sync → push the updated data to the repo |
| 1.2 | **Create `.github/workflows/backtest_check.yml`** | Triggered on every push or daily: run a backtest on the latest data, compare against baseline metrics, and alert if drift exceeds the threshold |
| 1.3 | **Create `.github/workflows/factor_analysis.yml`** | Run factor IC/IR analysis once a week, generate a report, and detect factor decay |
| 1.4 | **Store strategy parameters as artifacts** | Optimized parameters are stored in the repo as JSON/TOML, without depending on local files |
| 1.5 | **Solve the JQ credentials problem** | JQ account/password are passed into CI via GitHub Secrets; evaluate whether the JQ API allows logins from multiple IPs |

> **Note**: The GitHub Actions free quota is limited (~2000 min/month), so the data sync script must keep its run time under control. If JQ forbids simultaneous logins from multiple IPs, switch to local sync + Actions doing analysis only.

### Phase 2: Strategy Evolution Capability (4-6 Weeks)

**Goal**: give strategies the ability to "fine-tune automatically with the market" and reduce overfitting risk.

| # | Task | Description |
|---|------|------|
| 2.1 | **Implement a walk-forward backtest framework** | Add rolling-window train/validation separation on top of the existing backtest engine (e.g. 12 months of training → 3 months of validation, rolling forward) |
| 2.2 | **Simplify strategies to core factors** | Break down `csi1000_enhanced`, keeping ≤3 core factors + a clear economic rationale |
| 2.3 | **Introduce online parameter updates** | Use exponentiated gradient descent (EGD) to update strategy weights online, fine-tuning by ≤1% per day |
| 2.4 | **Strategy lifecycle management** | Record each strategy's "effective date" and "retirement conditions" (e.g. retire if the Sharpe ratio < 0.5 for 3 consecutive months) |
| 2.5 | **Ensemble of simple strategies** | An equal-weight/dynamically weighted ensemble of 2-3 complementary simple strategies |

### Phase 3: ML/AI Introduction (6-12 Weeks)

**Goal**: on the basis of stable strategies, gradually introduce ML auxiliary signals.

| # | Task | Description |
|---|------|------|
| 3.1 | **Anomaly detection module** | Use isolation forest / LSTM-autoencoder to detect anomalous market days as a risk-control input (pause trading) |
| 3.2 | **NLP financial-report analysis agent** | Use hermes-agent to batch-read financial-report PDFs/announcements and extract key signals (earnings beats, risk warnings, industry trends) |
| 3.3 | **Factor mining pipeline** | Automatically discover new factors from N financial-report dimensions + price-volume features, and add them to the strategy pool only after walk-forward validation |
| 3.4 | **Data feedback loop** | Live trading records → feature store → periodic retraining → parameter update → deployment (a complete MLOps cycle) |

### Phase 4: Multi-Asset Expansion (Long Term)

| # | Task | Description |
|---|------|------|
| 4.1 | **Fund screening module** | ETF/LOF screening logic (fees, tracking error, liquidity), reusing the existing backtest engine |
| 4.2 | **Bonds / convertible bonds** | If QMT supports bond trading, configure it on the QMT side; manage strategy parameters independently |
| 4.3 | **Asset allocation layer** | Add a stock/bond/fund asset-class allocation model (e.g. risk parity) above the strategy engine |

---

## IV. Architecture Decision Records

### ADR-1: GitHub Actions Only Orchestrates, Never Executes

- **Decision**: live trading is always executed on the local machine; GitHub Actions handles data sync, backtest validation, and alerting
- **Rationale**: security (no broker credentials exposed), compliance, and QMT cannot run in a CI environment
- **Alternatives**: everything local (simple but lacks automated validation); cloud server (expensive, and QMT still needs Windows)

### ADR-2: Keep the MiniQMT Gateway, Prepare a QMT Fallback

- **Decision**: continue with the MiniQMT + XtQuant gateway approach, and add compatibility with the full-featured QMT edition in `xtquant_gateway`
- **Rationale**: MiniQMT is the best option today (flexible API, native Python), and the XQuant API is compatible across both modes
- **Risk mitigation**: if MiniQMT is phased out, migrate to QMT + API Server mode (same API, only the connection method differs)

### ADR-3: Keep Strategies Simple, Keep Validation Strict

- **Decision**: at most 3 core factors per strategy; a strategy must pass walk-forward validation before going live; introduce online learning to fine-tune parameters
- **Rationale**: over-optimization + static parameters = overfitting. Simple strategies + online adaptation + strict validation = long-term survival
- **Anti-pattern**: compensating for overfitting with ever more complex strategies (makes the problem worse)

### ADR-4: Data Stays Out of git and Is Rebuilt with Scripts

- **Decision**: the whole `data/` directory is in `.gitignore`; each machine fetches data independently via the JQ sync scripts
- **Rationale**: SQLite files are large and binary and unsuitable for git diff; the JQ data source is unified, so every machine pulls identical results
- **Exception**: constituent-stock snapshots in `data/meta/` may be kept in git (small, text-based, rarely changed)

---

## VI. Implementation Progress (2025-07-30)

### ✅ Phase 0: Harden the Current Architecture

| Task | Status |
|------|------|
| .gitignore completed | ✅ git repository initialized, 156 files committed, data/ and .env excluded |
| System health check | ✅ `final_freeze_2025.toml` backtest passed: annualized return 39.8%, excess return +10.2%, Sharpe 2.02 |
| quota_blocked | ⏸ JQ quota issue, not a code problem; needs to be verified on a trading day |

### ✅ Phase 1: Introduce GitHub Actions

| Task | Status |
|------|------|
| `.github/workflows/backtest_check.yml` | ✅ Daily backtest + drift alerts, triggered by push + schedule |
| `.github/workflows/factor_analysis.yml` | ✅ Weekly factor IC/IR analysis, runs on Saturday |
| `.github/workflows/data_sync.yml` | ✅ Manually triggered mode consistency + JQ sync |
| `.github/workflows/jq_signin.yml` | ✅ Daily automatic JoinQuant check-in to claim credits (Playwright + captcha recognition) |
| Data moved out of the repo | ✅ `data/` → symlink → `~/HHD/stock/data/` (mechanical hard drive) |

### ✅ Paper-Trading Validation

| Step | Status |
|------|------|
| Paper-trading config | ✅ `config/live_paper.toml`: broker_type=paper, dry_run=true |
| Pipeline end to end | ✅ `run_daily_live_pipeline.sh` runs through (prepare-live-plan + archive) |
| .gitignore | ✅ data/, output/*, .env, uv.lock are excluded |

### ✅ Phase 2: Strategy Evolution Capability

| Task | Module | Description |
|------|------|------|
| walk-forward backtest framework | `quant/tools/walk_forward.py` | Rolling-window training/validation, hyperparameter optimization (top_k, stop-loss, entry interval), overfitting detection (parameter stability + excess-return persistence) |
| Simplified strategies | `quant/stock_strategy/simple_3factor.py` | 3-factor strategy (fundamentals + momentum + stability) and a 2-factor baseline (quality + momentum), registered in the registry |
| Online parameter updates | `quant/stock_strategy/online_learning.py` | EGD algorithm that fine-tunes factor weights daily based on the rank IC gradient, persisted to JSON |
| Strategy lifecycle | `quant/stock_strategy/lifecycle.py` | Three-state machine active → watch → retired, with automatic retirement based on rolling Sharpe/excess return/drawdown |

### ⏳ To Do

- [ ] MiniQMT gateway integration testing on a real Windows machine (requires enabling a broker quant account)
- [ ] After filling the gateway token into `live_qmt_http.toml`, switch to live-connected paper-trading mode
- [ ] Verify the first GitHub Actions run (check whether jq_signin works normally)
- [ ] ML anomaly detection module
- [ ] NLP financial-report analysis agent (hermes)
- [ ] Data feedback loop MLOps

---

## VII. Next Actions

1. [ ] **Enable a broker quant account** (Guojin Securities, QMT/MiniQMT)
2. [ ] Install MiniQMT on a real Windows machine + start `xtquant_gateway`
3. [ ] Change `broker_type` in `live_qmt_http.toml` to `qmt_http` and fill in the token
4. [ ] Check the result of the first run of the `jq_signin` workflow on GitHub
5. [ ] Run `git pull` weekly to sync code + strategy updates

---

<a id="2-user-trading-notes"></a>

# User Trading Notes (Recovered Version)

> Note: This file was compiled from the raw material you provided in this session, keeping the original meaning as far as possible and structuring it for later strategy implementation, post-trade review, and risk audits.

## I. Trading Background and Stages

- Market view: the A-share market is dense with retail investors; small-cap/micro-cap stocks are more volatile, with opportunity and risk coexisting.
- Personal path:
  - Spring Festival 2023 - 2023.11: from first exposure to live trading, about 8 months
  - 2023.11 - 2024.4: from live trading to quitting the job and trading full time, about 6 months
  - Current date: 2026.3
  - Current capital: about 20 million (CNY)
- Lessons learned: early value investing lost heavily; looking at PE/PB alone is not enough to cope with market behavior.

## II. Market Segmentation View

- CSI 300: heavily institutional, relatively stable volatility.
- CSI 500 / 1000 / micro-cap: higher retail participation, higher volatility, more pronounced potential excess return.
- Conclusion: small-cap strategies must strengthen execution and risk control; do not look only at “paper factor returns”.

## III. Paper Trading vs Live Trading Differences

## 1) Paper Trading

- Observe the whole market in layers (largest 10% by market cap, smallest 10% by market cap), rebalance periodically, and compare the profitability characteristics of large and small caps.

## 2) Live Trading

- The core question is not “is the signal good” but “can it be filled, and how costly is the fill”.
- Must be handled:
  - Trading volume/liquidity constraints
  - Large-order impact
  - Slippage
  - Order cancellation and order-chasing strategies

## IV. Key Execution-Layer Principles

## 1) Slippage Is a Profit Killer (Especially for Small Caps)

- Backtests must deduct strict slippage (for small caps, stress testing at the 0.1%~0.3% level is recommended).
- Only if there is still a profit after the deduction does live trading make sense.

## 2) 5-Minute Order-Timeout Cancellation Mechanism

- An order unfilled after 5 minutes usually means the market state has changed and should trigger a re-evaluation.
- Passive strategy: mainly cancel and re-place, to avoid being passively run through by the price.
- Active strategy: in a strongly trending market, switch to more aggressive execution (to avoid missing the move).
- Conclusion: time itself is a risk factor, so order-timeout cancellation is a necessary module.

## 3) TWAP / VWAP Order Splitting

- Split large orders into small ones to control impact cost and fillability.
- Linked with ADV/order-book-depth constraints to prevent excessive impact from any single order.

## V. Views on Quant Funds and Crowded Trades

- Do not blindly follow “star quant funds”.
- Core risk: alpha decays once a strategy becomes crowded, and the structure of the capital game changes.
- Conclusion: the window of opportunity is limited, so risk cost and execution quality must be taken seriously.

## VI. Current Live-Trading Methodology (Mechanical Execution)

- Open positions only under “moderate volatility” conditions.
- Trading timeframe leans medium-to-low frequency (1H/4H view, more stable execution).
- Per-trade loss control:
  - Fixed risk per trade does not exceed 1%
  - Maximum loss per stock 5%
  - Consecutive losses trigger a pause
- Profits are protected with a trailing stop-loss.

## VII. Position-Sizing Iron Rules

- Maximum risk per trade: <= 2%
- Total position: <= 50%
- Averaging down on losing positions is strictly forbidden
- After 2 consecutive losing days, stop trading for 1 day

## VIII. Suggestion: Validate Against Mutual-Fund Holdings

- Reference sources: Tiantian Fund (fund website), East Money (top fund holdings)
- Validation method: feed the top 20 holdings of mutual funds into the model and check whether the model's scores are consistent and whether there is systematic bias.

## IX. System Design Direction

- Short term (trial period): make full use of the available JQ window for strategy evaluation and parameter screening.
- Medium to long term (live trading): continuously accumulate QMT market + trading data into a local database for rolling re-evaluation.

---

<a id="3-live-execution-and-risk-control-rules"></a>

# Live Execution and Risk-Control Rules (Recovered Version)

## I. Core Goals

- Turn “backtestable returns” into real returns that are “fillable and sustainable”.
- In small-cap/micro-cap conditions, prioritize controlling execution risk and liquidity risk.

## II. Essential Execution-Layer Mechanisms

## 1) Order-Splitting Execution

- Use a TWAP/VWAP order splitter to break large orders into small ones executed across time slices.
- Each slice is subject to liquidity constraints (ADV / trading-value ratio / order-book depth).

## 2) Slippage Control

- The backtest side must deduct slippage cost up front.
- The live side records fill deviations dynamically, for parameter adaptation.

## 3) Order-Timeout Cancellation and Price Chasing

- Default rule: an order unfilled for more than 5 minutes triggers cancellation + re-evaluation.
- Passive execution mode: re-place the limit order to control impact.
- Active execution mode: after the trend is confirmed, fill priority may be raised (to avoid missing the move).

## III. Position and Risk Iron Rules

- Maximum risk per trade: <= 2%
- Total position: <= 50%
- Maximum loss threshold per stock: 5%
- 2 consecutive losing days: forced 1-day halt
- Averaging down on losing positions is prohibited

## IV. Monitoring-Layer Rules

- Daily layer: stock selection and rebalancing
- Intraday layer: minute-level monitoring of holdings (rapid drops / drawdowns / stop-loss triggers sell first)
- Close layer: archiving + daily report + risk review

## V. Post-Trade Review Fields (Recommended)

- Order time, fill time, order status
- Planned price vs average fill price (slippage)
- Cancellation reason, number of price chases
- Filled volume as a share of the day's trading volume
- Risk-trigger flags (stop-loss / drawdown / timeout)

## VI. Principles for Migrating to QMT Live Trading

- First validate the whole pipeline with PaperBroker
- Then integrate with the real QMT interface (account / holdings / place order / cancel order / reports)
- For every upgrade, dry-run first, then real positions

---

<a id="4-advantages-and-limitations-of-quant"></a>

# Advantages and Limitations of Quantitative Trading

> This document originates from a comparison table in a basic quant textbook ("advantages of quant vs limitations of quant"), extended with this repo's actual circumstances.
> Purpose: take a look in the mirror before writing a strategy, running a parameter search, or going live.

## I. Advantages vs Limitations Comparison Table

| Advantages | Limitations |
|------|------|
| **Discipline**: strict execution, free from emotional interference | **Black swans**: models fail in extreme markets |
| **Backtestable**: historical data can validate a strategy's effectiveness | **Overfitting**: the past does not represent the future |
| **Replicable**: the same strategy can be applied to many instruments | **Homogenization**: converging strategies lead to crowded trades |
| **High efficiency**: programs monitor 24 hours a day | **Lag**: slow reaction to sudden events |

## II. How Each Item Maps to Concrete Practice in This Repo

### 1) Discipline vs Black Swans

- Discipline:
  - The stop-loss / trailing stop-loss / position caps in `quant/execution/risk_guard.py` + `[strategy]` are the hard-coded execution discipline.
  - Pause 1 day after 2 consecutive losing days (`pause_after_consecutive_loss_days = 2` / `pause_after_loss_pause_days = 1`).
- Black-swan protection:
  - Crash thresholds: `crash_ret5_threshold = -0.08`, `crash_ret20_threshold = -0.14`, `crash_breadth_threshold = 0.25`.
  - Once triggered, enter a cash-only cooldown period of `crash_cooldown_days`.
  - Maximum loss per position `max_single_position_loss_pct = 0.05`; total exposure is capped by `max_total_exposure_pct`.
- **Still must admit**: in extreme markets (e.g. a policy announced overnight, an index falling 10% in a single day) any quant model will fail. **Risk control can only limit losses; it cannot hedge such events**. This is why you must hold cash and low-correlation assets.

### 2) Backtestable vs Overfitting

- Backtestable: `main.py --mode backtest`; outputs `output/backtest_metrics.json` + `backtest_equity_curve.csv` + `backtest_trades.csv`.
- Overfitting protection:
  - `main.py --mode walk-forward` performs rolling train/validation separation (`quant/tools/walk_forward.py`)
  - `main.py --mode factor-analyze` checks whether single-factor IC/IR is decaying
  - ADR-3: at most 3 core factors per strategy (see `docs/10_foundation/01_evolution_plan.md`)
- **Still must admit**: any backtest is an out-of-sample gamble of “past vs future”; the final validation can only come from paper trading and small-capital live trading.

### 3) Replicable vs Homogenization

- Replicable: a cross-sectional strategy only needs a different stock universe (CSI 300 / CSI 500 / CSI 1000 / CSI 2000); the factor logic does not change (see `docs/20_strategy/01_cross_section_playbook.md`).
- Homogenization:
  - **Risk**: the multi-factor combination of small market cap + low valuation + reversal + high ROE is one of the most crowded tracks in the whole market, and alpha will be eroded.
  - Response:
    - Keep strategies simple and highly explainable, so it is easy to tell whether your own alpha is still there
    - Run IC/IR analysis periodically to see whether factors are decaying
    - `docs/60_regulation/01_quant_regulation_playbook.md`: adjust trading pace in step with tightening regulation

### 4) High Efficiency vs Lag

- High efficiency: `main.py --mode live-daemon` + `--mode live-auto`; `quant/live/daemon.py` monitors around the clock.
- Lag:
  - A daily-frequency strategy reacts slowly when major news breaks intraday.
  - `quant/live/intraday_monitor.py` provides minute-level sell monitoring as a partial compensation.
  - But we **do not aim to build a “news-reaction strategy”** — that is a different track that requires low-latency infrastructure and information sources.
- Future path (`docs/70_ai_research/01_ml_ai_integration_plan.md`): use NLP/agents to read financial reports and announcements and produce daily-frequency structured signals, rather than chasing intraday news.

## III. Put This Table on the Desk Where You Write Strategies

Whenever you are about to add a "more complex" factor, a "finer" parameter, or a "fancier" architecture (RL, deep learning, Chanlun pattern recognition ...), first ask yourself 4 questions:

1. **Overfitting**: can this change pass walk-forward?
2. **Homogenization**: is this factor/signal something everyone in the market is already using?
3. **Black swans**: how would this change fare in the 2015 stock-market crash / 2016 circuit-breaker / 2020 pandemic / 2024 Spring Festival small-cap collapse?
4. **Lag**: is this change betting on "being able to react before the market"? If so, on what basis?

**If any one of them cannot be answered clearly, do not go live yet.**

## IV. Relationship to Other Documents in the Repo

- Relationship to `10_foundation/01_evolution_plan.md`: this document is the "why"; that one is the "how".
- Relationship to `10_foundation/03_risk_rulebook.md`: this document acknowledges that "quant will fail"; that one stipulates that "when it fails, losses must be reduced automatically".
- Relationship to `20_strategy/*`: this document is the criterion for screening new strategies; those documents are the concrete rules for strategy implementation.

---

<a id="5-strategy-lifecycle-freshness-decay-elegy-and-evolution"></a>

# Strategy Lifecycle: Freshness, Decay, Elegy and Evolution

> Case source: `csi1000_enhanced` + `config/final_freeze_2025.toml` and its JoinQuant port `test/cc_op1.py` (validated 2025–2026).
> Conclusion first: **some strategies were not “badly written”; they “outlived their era”. Public circulation accelerates death, but it is not the only cause.**

## 5.1 Elegy: `csi1000_enhanced` / `cc_op1`

**Banner**: Deified in backtest, dust out of sample

**Upper couplet**: Five-factor cross-section, in-sample annualized return of forty percent, Sharpe above two, as if divinely aided

**Lower couplet**: Once ported to JoinQuant, three straight losing months OOS, turnover doubled, alpha finally lost

**Eulogy (three sentences)**:

1. In the 2025 SQLite data you were the champion — annualized return +39.8%, excess return +10.2%, maximum drawdown only -10.1%.
2. You missed the entire 2026 Q1 rebound, and then turned the “engine-aligned” sell rules in v1.1 into **corpse-flogging daily turnover**; commissions and slippage ate the remaining alpha.
3. You are not the first strategy to fall on small-cap value-momentum factors, and you will not be the last; rest in peace, and let walk-forward and factor IC keep vigil over you.

`test/cc_op1.py` is marked as the **elegy edition (archived)**: for reference only on “porting distortion” and “out-of-sample decay”; **it must not go live, and its parameter details must not be shared externally again**.

## 5.2 Why It Got “Worse the More It Was Changed” on JoinQuant

Putting the local backtest and the JoinQuant results side by side, the gap is not a single bug but **three layers stacked together**:

| Layer | Symptom | Mechanism |
|------|------|------|
| **A. Out-of-sample regime switch** | Strong IS in 2025, OOS collapse in 2026 Q1 (see `reports/migration_check_2026.md`) | Small-cap + value + momentum faced headwinds in 2026 Q1; the strategy held conservative positions while the benchmark surged → excess return fell off a cliff |
| **B. Porting-definition distortion** | The same parameters fail to beat the benchmark on JQ | Factor publication timing, ST/suspension filtering, limit-up/limit-down fills, and the fee model are not fully consistent with the local SQLite engine |
| **C. v1.1 “over-alignment”** | Returns got worse after the change, and giant daily PnL bars became denser | Stuffing **daily-frequency selling** (ranking percentile 0.94, MA20 breakdown, forced liquidation on crash) into the daily-frequency script → turnover soared from the “251 trades a year” level to “over a hundred trades in half a year and still losing”, i.e. **negative-expectancy turnover** |

**Three especially fatal changes in v1.1** (do not copy them verbatim in any future port):

1. **Mistranslation of `SELL_RANK_PERCENTILE_THR = 0.94`**  
   Locally, `sell_score_threshold` is used **on the holdings subset + days held + in combination with other sell rules**; the JQ version became “sell if ranked in the bottom 6% of a 400+ stock pool” → it sells almost every day, and **friction costs dominate PnL**.
2. **Forced liquidation on crash days**  
   Locally, after `_is_market_crash` exposure→0 comes with a state machine and a re-entry cadence; the JQ version sat in cash on the first day of the rebound → **sold at the bottom**.
3. **Consecutive MA20 breakdowns**  
   CSI 1000 constituents are volatile; a 2-day breakdown in a choppy market = repeated stop-outs; combined with ranking-based selling, the daily PnL chart shows **dense giant orange bars** (as seen in the screenshot).

**Lesson**: the “correct logic” in the backtest engine does not equal a “line-for-line” JoinQuant script. **First verify that the factor ranking is consistent, and only then discuss sell rules; changes to sell rules must be A/B-tested on their own and must not be bundled into an “aligned edition”.**

## 5.3 Were Strategies “Doomed from the Start”? Does Public Sharing Accelerate Decay?

**Yes, but the causes of death need to be told apart:**

| Cause of death | Common? | Case in this repo |
|------|-------------|-----------|
| **Overfitting** (parameters fit historical noise) | Yes | `final_freeze_2025` was excellent in 2025 and dropped to zero in 2026 Q1 → a typical IS/OOS split |
| **Regime switch** (macro / style rotation) | Yes | In 2026 Q1 small caps were weak and the index strong, so the cross-sectional long-short factors failed |
| **Crowded trades** (homogenization) | Yes, and accelerating | Value + quality + momentum + low-volatility is one of the most frequent combinations on the JoinQuant forum |
| **Public circulation** | **An accelerant, not the only cause of death** | Posts/code spread → more people trade the same direction → factor IC decays faster; but **unpublished strategies also die from regime switches** |
| **Execution and data differences** | Specific to porting scenarios | `cc_op1` is inconsistent with the local engine → amplified losses |

**On “gradually stopping working after wide circulation on the JoinQuant forum”**:

- **Academia and practice agree**: published factors (especially simple price-volume/fundamental ones) suffer **alpha decay**; the wider the circulation, the faster the arbitrage.
- **But it is not “share and die”**: what dies is **that fixed set of parameters + fixed factor combination**; what lives is **a process of continuous validation that can retire strategies and swap factors**.
- **For individual quant traders**: what is truly scarce is not “some frozen toml” but **(1) a data pipeline (2) validation discipline (3) risk control (4) knowing when to let go**.

## 5.4 How to Keep Evolving and Distill Your Own Strategies (Actionable Checklist)

Do not chase “finding a holy-grail strategy”; chase **“a pipeline that can repeatedly produce new strategies”**. This repo already has the tools; run them in a quarterly cycle:

### Step 1: Accept That Strategies Have a Lifespan (Retirement Criteria)

Meeting **any two** of the following puts a strategy into **retirement review** (no adding leverage, no scaling up capital):

| Signal | Threshold (example) | Tool |
|------|-------------|------|
| Rolling OOS excess return | < 0 for 2 consecutive quarters | `main.py --mode walk-forward` |
| Core factor IC | 12-month mean < 0.02 or IR < 0.3 | `main.py --mode factor-analyze` |
| Paper trading vs backtest slippage | Live/paper annualized return lags the backtest by > 5% | `main.py --mode live-auto` + daily report |
| Independent JoinQuant validation | The same logic's JQ backtest underperforms the benchmark for > 1 year | `test/*.py` standalone scripts |

**Retirement actions**: freeze the config (rename it to `*_retired_YYYYMMDD.toml`), keep the strategy code but have `registry` not select it by default; write a 3-line eulogy into `reports/` or this section's case table.

### Step 2: A Quarterly “Factor Check-Up”

```bash
uv run python main.py --mode factor-analyze --config config/final_freeze_2025.toml
uv run python main.py --mode walk-forward   --config config/final_freeze_2025.toml
```

Record: an `output/` summary + a one-line conclusion in `reports/` — **which factors are still alive and which are dead**.

### Step 3: Iterate in Small Steps, No Bundled Big Changes

- Change only **1 dimension** at a time: pick one of factor weights / top_k / sell threshold / regime threshold.
- Reports must cover **both IS and OOS periods** (repo convention: 2025 IS + first half of 2026 OOS).
- JoinQuant porting: **port only the scoring + top_k buying first**, keeping only the hard stop-loss for selling; ranking-based selling, MA breakdowns, etc. should be **validated on a separate branch**.

### Step 4: Use “Branch Configs” Instead of Overwriting the Frozen Version

```
config/final_freeze_2025.toml          # Retired, do not modify
config/csi1000_refit_2026H2.toml      # New candidate; freeze only after full validation
config/branch_alpha_regime_switch.toml # Regime experiment branch
```

Naming convention: `final_freeze_*` = snapshots that went live or were seriously considered for going live; `branch_*` = experiments; `*_retired_*` = elegies.

### Step 5: 1 Week of Paper Trading → Small Capital → Only Then Talk About Scaling Up

See `docs/05_live.md`; **a profitable JoinQuant backtest ≠ ready for live trading**, especially for small-cap high-turnover strategies.

### Step 6: Learn the “Structure” from the Community, Do Not Copy the “Parameters”

When reading `test/jq_horse.py` and `test/wufu.py`, extract only:

- Scheduling framework (`run_daily`, sub-strategy capital ratios)
- Tradability filters (ST, trading suspension, limit-up/limit-down)
- Risk-control shell (stop-loss, cooldown period)

**Do not copy**: specific magic numbers, the “optimal parameters” in forum posts, and “100x annualized” combinations that have not been through OOS.

## 5.5 Repo Files Related to This Section

| File | Purpose |
|------|------|
| `test/cc_op1.py` | Elegy-edition JQ script; records the lessons of the failed port |
| `reports/migration_check_2026.md` | Source of the local IS/OOS numbers |
| `config/final_freeze_2025.toml` | Frozen; **no longer used for new capital by default** |
| `docs/03_engine.md` §4 | Stopping rules to halt overfitting |
| `docs/06_regulation.md` | Frequency-reduction template for when regulation tightens |

## 5.6 Four Sentences to Remind Yourself

1. **The backtest is the eulogy, paper trading is the autopsy, and whether live trading is the funeral or a resurrection depends on OOS.**
2. **The miracle strategies on forums mostly die of crowding; even if you never shared yours, it can still die of a regime switch.**
3. **One sell-rule line in the engine can become ten losing trades once ported to JoinQuant.**
4. **The goal of evolution is not never to retire, but to have the next candidate waiting in the walk-forward queue when you do.**
