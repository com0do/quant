# Migration & Parameter Stability Verification Report (2026-08-14)

## 1. Migration Scope

Moved from `~/test/quant` (early iteration repo, no remote access) into the current repo:

| Item | Source | Target | Size |
|----|------|------|------|
| CSI 1000 market data | `data/runtime/market_csi1000_latest.db` | Same name | 353 MB |
| CSI 2000 market data | `data/runtime/market_csi2000_latest.db` | Same name | 194 MB |
| CSI 300/500 market data | `data/runtime/market_csi300_500_latest.db` | Same name | 51 MB |
| Index constituents meta | `data/meta/index_constituents.db` | Same name | 12 MB |
| JQ account + proxy | `.env` | Same name (gitignored) | 251 B |

**Not migrated**:
- `~/test/quant/data/philip/` (18 GB of daily/minute high-frequency cache, not referenced by current code)
- `~/test/quant/data/runtime/market_philip_csi2000_2025_validation.db` (244 MB validation set,
  not referenced by the config)

**Config**: already consistent (`config/*.toml` is byte-identical to the legacy copy).

**Code fix**: `JQDATA_USERNAME/PASSWORD` in `.env.example` did not match what the code actually reads
(`CC_JQ_NAME/CC_JQ_PW`); it has been changed back, and a comment about the HTTP proxy was added.
(Later renamed to `JQ_USERNAME/JQ_PASSWORD` everywhere.)

## 2. Current State of the JoinQuant Data

| DB | Start date | End date | Trading days |
|----|--------|--------|--------|
| CSI 1000 | 2024-12-24 | **2026-03-13** | 293 |
| CSI 2000 | 2024-12-24 | **2026-03-13** | 293 |
| CSI 300/500 | 2024-12-16 | 2025-12-31 | 255 |

## 3. Incremental JQ Sync — **Network Currently Unavailable**

Diagnosis:
- TCP probe `39.107.190.114:7000` (JoinQuant thrift port) → **BLOCKED**
- HTTPS 443 to `www.joinquant.com` → reachable (via the corporate Fortinet proxy)
- Conclusion: the corporate firewall only allows HTTP/HTTPS and does not pass raw TCP; the JQData SDK uses thrift RPC over port
  7000, so HTTP_PROXY is not enough, and a SOCKS proxy / VPN is required to backfill the data

**CSI 1000 currently already has 51 trading days of naturally out-of-sample data, from 2026-01-02 → 2026-03-13**,
which is enough to verify whether the frozen parameters have stopped working, even before getting back online.

## 4. `config/final_freeze_2025.toml` Parameter Stability Test

The same strategy was run over three periods (`csi1000_enhanced`, `top_k=4`, `buy_score_threshold=0.00`,
`sell_score_threshold=0.94`, regime-switch enabled).

| Period | Annualized return | Benchmark annualized | Excess | Sharpe | Maximum drawdown | IR | Trades | Ending equity |
|------|----------|----------|------|--------|----------|------|--------|----------|
| **2025 full year (IS)** | **+39.80%** | +29.64% | +10.17% | **2.02** | -10.12% | +0.39 | 251 | 136 796 |
| **2026-01 → 03-13 (OOS)** | -31.98% | +40.22% | **-72.20%** | -1.58 | -12.40% | -3.67 | 55 | 93 593 |
| **2025 + 2026-Q1 combined** | +34.27% | +35.95% | **-1.67%** | 1.80 | -10.12% | -0.07 | 307 | 139 653 |

**Key observations**:

1. **Strong in-sample**: 2025 full-year annualized return of 40%, Sharpe 2.0, maximum drawdown 10%, 251 trades, IR +0.39 —
   the parameters remain stable over the in-sample period.
2. **Lagging out-of-sample**: in 2026 Q1 the CSI 1000 benchmark ran at +40% annualized (+8% in 2 months), while the strategy was -6.4%
   (equity 100k→93.6k), mostly during mid-February to early March — the strategy held conservatively and missed the rebound.
3. **Overall, alpha is zeroed out**: computing IS+OOS together, 34.27% annualized vs the benchmark's 35.95%, **the excess has been pressed
   down to -1.67%** — the excess accumulated in 2025 was eaten up entirely by 2026 Q1.
4. The two risk-control metrics, drawdown and volatility, remain manageable (combined-period maximum drawdown -10.1%, annualized volatility 19.1%);
   the strategy "didn't blow up", but it has **clearly lost its Alpha**.

## 5. Conclusions & Recommendations

- **The parameters no longer "maintain returns"**: the frozen version already underperformed the benchmark by 14 percentage points in 2026 Q1; do not blindly go live.
- Suggested order:
  1. After setting up SOCKS/VPN, first run `main.py --mode jq-bulk-sync` to backfill CSI 1000/2000
     data through 2026-08-13 (today), so that at least 5 months of new data are available.
  2. Run `main.py --mode walk-forward --config config/final_freeze_2025.toml`,
     using rolling windows to re-verify whether 2026 Q1/Q2 formed a new regime.
  3. Run `main.py --mode factor-analyze` to see whether any core factor has decayed (IC dropped a notch).
  4. Use `--mode optimize-fast` to re-fit on 2025 IS + 2026 Q1 OOS and produce a new
     freeze version (`config/final_freeze_2026H1.toml`), rather than continuing to use the 2025 frozen one.
- **Do not** switch this `final_freeze_2025.toml` to `dry_run=false` and go live.

## 6. Artifacts

- `output/migration_check/metrics_baseline_2025.json`
- `output/migration_check/metrics_oos_2026q1.json`
- `output/migration_check/metrics_full_2025_to_2026q1.json`
- One corresponding equity curve CSV for each

(All are under `output/`, which is excluded by `.gitignore`; only this report is committed to git.)
