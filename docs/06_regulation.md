# Playbook for Quantitative-Trading Regulatory Pressure

> Reality check: as of writing, quantitative trading accounts for close to 20% of total A-share trading value, and regulators and exchanges are discussing 25% as the signal threshold for "further tightening". Relevant policy tools include **window guidance, program-trading reporting, order-cancellation frequency limits, tighter abnormal-trading definitions, and differentiated fees**.
>
> Goal of this playbook: let this repo **adapt automatically** at every step of regulatory tightening, and avoid the account being classified as "under key monitoring" or "abnormal trading".

---

## 1. Possible Regulatory Measures and Their Impact

| Measure | Direct impact on individual quant traders |
|------|-----------------------|
| **Program-trading flag reporting** | The account must be flagged as program-trading on the broker side; once flagged, cancellation/order counts are tracked separately |
| **Cancel-to-order ratio limits** | A daily cancellations / orders ratio above the threshold triggers throttling or warnings |
| **Abnormal-trading definition** | Frequent order-and-cancel, large resting-and-cancelled orders, price pushing/suppressing, etc. can be classified as abnormal trading |
| **Differentiated cancellation fees** | Per-cancellation fees rise sharply for accounts with high cancellation frequency, directly eating the profit of small-cap strategies |
| **Window guidance** | Exchanges may unilaterally discourage high-frequency accounts (e.g. banning order submission, per-day throttling) |
| **Execution speed limits** | In extreme cases, order intervals and the minimum time per slice may be given a mandatory floor |
| **Information disclosure delay** | The Shenzhen Stock Exchange is already discussing delayed market-data delivery for program-trading accounts |

Not all of these will necessarily appear at once, but **each one can turn a strategy that was "break-even" into a losing one**.

## 2. This Repo's General Response

**Overall principle**: **"Look like a human + the fewer trades the better + make every trade complete in one go whenever possible."**

Broken down into 6 points:

1. **Lower total trading frequency**: move the rebalance cycle from daily to 3-5 days or even weekly.
2. **Lower cancellation frequency**: lengthen the order-timeout cancellation threshold and reduce the number of chase attempts.
3. **Lower per-symbol intraday count**: at most 2 orders per symbol per day (including cancel-and-replace).
4. **Lower the slice count**: reduce TWAP slices from possibly 8-10 down to 3-5.
5. **Lower order-submission density**: no dense resting orders of 100 shares; when splitting orders is necessary, split down to a mid-range size.
6. **Proactively report the program-trading flag**: file a compliant declaration with the broker; being on the regulator's "compliant program-trading" list is actually safer.

## 3. Parameter-by-Parameter Adjustments (copy into config/*.toml)

The parameters below already exist in `quant/config.py`; you only need to tighten them in the config.

### 3.1 Trading Frequency

| Parameter | Current (`final_freeze_2025.toml`) | Suggested after tightening |
|------|----------|-----------|
| `strategy.entry_interval_days` | `2` | `5` (weekly rebalance) |
| `strategy.min_hold_days` | `2` | `5` |
| `strategy.top_k` | `4` | `6-8` (raise the candidate count as the rebalance scope shrinks) |

### 3.2 Cancellation and Chasing

| Parameter | Location | Current default | Suggested after tightening |
|------|------|----------|-----------|
| `daemon.order_timeout_sec` | `DaemonConfig` | `300` (5 minutes) | `900` (15 minutes) |
| `daemon.max_chase_count` | `DaemonConfig` | `2` | `1` |
| `daemon.chase_step_bps` | `DaemonConfig` | `8.0` | `4.0` (smaller chase step) |
| `daemon.buy_chase_mode` | `DaemonConfig` | `"passive"` | Keep `"passive"` |
| `daemon.sell_chase_mode` | `DaemonConfig` | `"aggressive"` | Change to `"passive"` (unless the crash cooldown is triggered) |

### 3.3 Slice Density

| Parameter | Current | Suggested after tightening |
|------|------|-----------|
| `daemon.twap_slices` | `4` | `3` |
| `daemon.slice_interval_sec` | `60` | `120` (longer interval) |
| `daemon.max_daily_notional_per_stock` | `2,000,000` | Scale down to 15-20% according to account size |

### 3.4 Per-Day, Per-Account Caps

| Parameter | Location | Suggested after tightening |
|------|------|-----------|
| `risk.max_daily_orders` | `RiskConfig` | Reduce from `100` to `40-60` |
| `risk.max_daily_notional_pct` | `RiskConfig` | Reduce from `1.2` to `0.6-0.8` |

### 3.5 Hard Protection on the Gateway Side

- `xtquant_gateway` (see `docs/50_live/03_xtquant_gateway_v2.md`) already has a server-side risk-control wall. **Add** the following in `xtquant_gateway/core.py` or `settings.py`:
  - A daily cancellation cap (e.g. at most 30 cancellations per account per day)
  - A per-symbol daily order cap (e.g. at most 2)
  - A minimum resting time per order (to avoid "instant cancels")

These are **pending** tasks; see the "Code TODO List" at the end of this document.

## 4. Strategy-Side Selective Avoidance

- **Prefer low-turnover strategies**: multi-factor + weekly cross-sectional (`csi1000_enhanced` + `entry_interval_days = 5`) is naturally low-turnover.
- **Prefer to avoid high-turnover scenarios**:
  - Minute-level chart-pattern recognition
  - Intraday T+0 arbitrage (A-shares do not allow it per se, but many quants do equivalent T+0 using collateral; this must stop)
  - Grid-style "buy low, sell high" trading
- If integrating Turtle trading (see `docs/20_strategy/04_strategy_schools_survey.md`), cut the number of add-ons from the traditional 4 to 2 to reduce order counts.

## 5. Account and Compliance

1. **Talk to the broker**: confirm whether the account is already on the program-trading list; if not, proactively file a compliant declaration.
2. **Self-limit single-account size**: keep a single account below the "key monitoring" threshold (definitions differ by exchange, usually based on "average daily trading value" or "order count").
3. **Spread across accounts**: if the household has multiple accounts, it is **not recommended** to run the same strategy on several accounts at once, since that is easily classified as "same-source program trading". Run different parameters or different strategies instead.
4. **Separate signals from execution**: use one identity for strategy research and data sync (cloud/research account) and another for live execution (local + QMT) to avoid cross-contamination of risk.

## 6. The Role of GitHub Actions and the Cloud

- `ADR-1` already states: **GitHub Actions only orchestrates and never executes**. This matters even more in a tightening regulatory environment.
- Cloud responsibilities: data sync, backtest drift, factor analysis, alerting.
- Local responsibilities: live order placement, market-data subscription, exception handling.
- **Never put trading credentials (broker login, QMT token) in the cloud**, even encrypted; if traced, the cost of explaining "cloud order placement" is extremely high.

## 7. Monitoring and Early Warning

- Add the following metrics to the daily archive (partly already in `quant/live/daily_report.py`):
  - Total orders, total cancellations, cancel-to-order ratio
  - Max order count for a single symbol
  - Total TWAP slice count
  - Average resting time per order
- Alert on any of the following conditions and **automatically reduce frequency the next day**:
  - Cancel-to-order ratio > 30%
  - Daily order count > `risk.max_daily_orders * 0.8`
  - The broker sends a risk-warning SMS in any form

How automatic frequency reduction would be implemented (not yet implemented): `quant/live/daemon.py` reads yesterday's archive and, if a warning is triggered, temporarily doubles that day's `entry_interval_days` (× 2) and temporarily reduces `twap_slices` by 1 (-1).

## 8. Code TODO List

The following are **not yet implemented**; they are features to add based on this playbook. They are listed here as issues, and any agent modifying code should look here first:

- [ ] `xtquant_gateway`: add a daily cancellation cap, a per-symbol daily order cap, and a minimum resting time per order.
- [ ] `quant/live/daemon.py`: add automatic frequency-reduction logic based on yesterday's archive.
- [ ] `quant/live/daily_report.py`: explicitly output the cancel-to-order ratio, max order count for a single symbol, and total slice count.
- [ ] `config/live_qmt_http.toml` / `config/live_paper.toml`: add the tightened-parameter templates from "3.2/3.3" of this document (commented out at first, enabled when ready to switch).
- [ ] `docs/30_engine/01_engine_and_parameters.md`: add a section "Recommended parameters in regulatory-tightening mode".

## 9. When to Exit Quant Trading

- Cancellation fees rise to the point of eating more than 80% of the small-cap strategy's annualized excess return.
- The broker restricts the account's available order count below what the strategy needs.
- The regulator classifies our account as "abnormal trading" and issues a written warning.

When any of these occurs, the first reaction is **not to look for a workaround**, but to evaluate switching to a lower frequency (e.g. monthly, or just holding funds).

---

## 10. Update History

- First version: compiled the regulatory trends + parameter adjustment plan + code TODO. The repo does not yet implement automatic frequency reduction or the gateway-side cancellation-frequency cap; the document explicitly marks them as pending.
