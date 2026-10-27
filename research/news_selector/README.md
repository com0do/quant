# News / policy / announcement / fundamentals selector (MVP, unverified)

Status: **research only, MVP, not validated for live trading.** This directory asks whether the "main up-wave is an
emotional response to good news" thesis can be turned into a daily A-share selector. The answer in this five-month
test is: not robustly. The most useful result is methodological — selecting the universe by *current* liquidity
creates a large look-ahead bias that made a weak strategy look spectacular.

## 1. What the selector does

At the close of day `t` it combines, using only information available up to `t-1`:

- **Corporate announcements** from the exchange disclosure feed, classified into positive / negative catalysts with
  a transparent finance lexicon. Positive items accumulate over 5 trading days; any negative item in the last 10
  days excludes the stock.
- **Policy news** from CCTV transcripts, mapped through a theme dictionary to Shenwan level-1 industries. Positive
  policy items accumulate over 5 trading days.
- **Limit-up reasons** from the Tonghuashun pool, used as an industry heat proxy (limit-ups in the stock's industry
  over the last 5 days).
- **Fundamentals** from quarterly reports, used only after a conservative publication lag (90 days for quarterly,
  120 days for annual): ROE, revenue YoY, net-profit YoY, gross margin, debt ratio.
- **Technical confirmation**: MA5 > MA10 > MA20, 5-day strength, volume expansion, distance to the 20-day high.

The final `ann_fund` variant is:

```
ann_pos_5 > 0, ann_neg_10 == 0, tech_score > 0, fund_score > 0
rank = z(ann_pos_5) + 0.5*z(tech_score) + 0.2*z(fund_score)
```

Top 3 names per day, 30% of equity each, entry at the next open (skip a limit-up open), exit after 5 trading days,
on a green bar, or on an 8% stop. Costs: 0.03% commission, 0.05% stamp duty, 0.1% slippage. Liquidity floor: 50M CNY
20-day average turnover.

The other variants are ablations: `tech` (technical only), `announcement` (positive announcements + technical,
negative excluded), `policy` (policy news + technical), `fundamental`, `policy_tech`, `multi`, and `combined`
(a weighted blend of news, fundamentals and technical).

## 2. Data (local, not checked in)

`quant/stock_data/news_store.py` is the data layer; it knows nothing about strategies. The default SQLite file is
`data/news/news.db` (gitignored). Market bars are read from `data/market/kline` (a local symlink to the existing
cache) and the point-in-time universe is `data/market/selector_universe_pit.csv`.

Fetched for this study (2026-05-01..2026-10-26):

| Table | Rows | Source |
|---|---:|---|
| announcements | 197,711 | akshare `stock_notice_report` by trading day |
| policy_news | 2,372 | akshare `news_cctv` by calendar day |
| limit_up | 8,486 | Tonghuashun limit-up pool, 117 trading days, via `scripts/news_archive.py` |
| fundamentals | 11,533 | akshare `stock_financial_analysis_indicator` for the 800-stock universe |
| market_news | 271 | akshare Eastmoney / CLS / THS flashes (recent-only) |

The fetchers are idempotent:

```bash
uv run --with akshare python research/news_selector/fetch_data.py --start 2026-05-01 --end 2026-10-26
uv run --with akshare python research/news_selector/fetch_fundamentals.py \
    --universe data/market/selector_universe_pit.csv --limit 800
# limit-up backfill (one year):
uv run python scripts/news_archive.py --sources ths_limit_up --backfill-days 400
```

## 3. The most important result: universe look-ahead

The first run selected the 800 most liquid stocks using the **last rows of the file (October 2026)** and then
backtested from May 2026. That is look-ahead: a stock that only became liquid later was already eligible. It made
`ann_fund` return **+100.0%** over five months (Sharpe 3.3), and most other variants looked good too.

Rebuilding the universe **as of 2026-03-01** (`build_universe.py`) removed that bias and the result collapsed:

| Variant | Full (May-Oct) | May-Jul | Aug-Oct |
|---|---:|---:|---:|
| Equal-weight universe | -19.3% | -13.6% | -6.6% |
| CSI 1000 | -22.7% | -17.5% | -6.3% |
| technical only | -36.1% | -13.5% | -34.6% |
| announcement (negative excluded) | -16.9% | -1.2% | -14.3% |
| **ann_fund** | **+9.9%** | **+16.6%** | **-13.4%** |
| policy + technical | -37.8% | -19.7% | -27.0% |
| fundamental only | -34.7% | -16.4% | -14.1% |
| combined | -14.8% | -8.8% | -1.6% |

`ann_fund` is the only variant positive over the full five months, but it loses 13.4% in Aug-Oct and its t-stat is
0.34 (t-stats by trade: +0.34 full, +0.61 May-Jul, -1.61 Aug-Oct). The all-component `combined` variant is negative
in every period. Nothing survives both subperiods.

The lesson is worth more than the strategy: **build the universe at the test start**, not from today's liquidity
ranking. The same correction turns the technical-only return from +52% (October universe) to -36% (March universe).

## 4. Component findings

- **Positive announcements are the least-bad news component**, but still negative over the full period (-16.9%);
  excluding negative announcements matters (on the earlier contaminated universe the unfiltered version was -26.8%).
  It was -1.2% in May-Jul and -14.3% in Aug-Oct; not statistically strong.
- **Policy news did not help in either subperiod** (May-Jul -19.7%, Aug-Oct -27.0%; full -37.8%). The
  CCTV-to-industry mapping is coarse and the signal is negative.
- **Fundamentals are weak**: +8.1% on the contaminated universe but -34.7% on the point-in-time universe; the
  report-date + 90/120-day lag is conservative, but the selected quality/growth names underperformed.
- **Technical momentum alone is not a free lunch**: it looked best under the look-ahead universe and was the worst
  in the corrected one.
- The all-component blend was negative; adding weak components diluted the one stable component.

## 4.1 Conditional event study: policy/industry heat vs company announcements (2024-2026)

The deck's 21 cases had a company catalyst before the move in only 4 of 21 names (about 20%); the rest started on
industry or policy news. The selector test above asks "can we pick stocks?"; this event study asks the more direct
question: **conditional on a strong-dragon signal, does policy/industry heat make the forward return stable, and are
company-catalyst signals better or worse?**

Data: 40,723 tradable strong-dragon signals (2024-01-02..2026-10-23, excluding limit-up-open fills), 5,094 codes;
8,491,580 industry-day rows from the local daily bars; 12,287 CCTV policy items (11,035 in 2024-2026); 786,734
announcement titles (2024-01..2025-03 and 2026-05..10; the 2025-04..2026-04 announcement window was not fetched, so
the announcement by-year table only shows 2024 and 2026). Forward return = next open -> close 5 trading days later;
t is computed from daily signal means.

| Group | n | mean 5d | median 5d | win | daily t |
|---|---:|---:|---:|---:|---:|
| all signals | 40,636 | -0.61% | -1.88% | 40.7% | -3.92 |
| industry heat top 20% | 13,034 | -0.06% | -1.59% | 41.8% | -2.04 |
| policy heat top 20% | 11,007 | -0.63% | -1.84% | 40.8% | -3.10 |
| policy top + industry top | 4,624 | +0.51% | -1.16% | 44.5% | -0.33 |
| positive announcement | 3,798 | -1.81% | -2.78% | 37.0% | -2.92 |
| no announcement | 32,339 | -0.29% | -1.63% | 41.7% | -3.52 |
| policy top + positive announcement | 1,418 | -1.04% | -2.33% | 38.8% | -1.30 |
| industry heat bottom 20% | 445 | +0.55% | -1.06% | 44.0% | +0.34 |

By year, the best group (`policy top + industry top`) is +1.16% in 2024, +1.03% in 2025 and -0.96% in 2026; the
pooled daily t is -0.33. It is not a stable positive edge.

Reading:

- Policy/industry heat **improves the relative result** (all -0.61% vs policy+industry top +0.51%) but not enough to
  be reliably positive; the best group loses in 2026.
- Company-catalyst signals are the **worst** group (-1.81%; no announcement -0.29%). This is consistent with the
  deck's ~20% observation: announcements describe some winners but are already priced in by the time they are public;
  the other 80% are policy/industry stories that a title-level announcement feed cannot capture.
- The industry-bottom group is positive in this sample but has only 445 signals and a t of +0.34; not evidence.
- Conclusion: the 80/20 split is **descriptively** right, but it does not produce a stable strong-dragon add-on.
  A policy/industry filter can reduce the loss; it does not turn the signal into a positive strategy.

Caveats: the CCTV → Shenwan-1 mapping is coarse (a title may mention many themes); industry membership is today's
snapshot; announcement coverage is incomplete for 2025-04..2026-04; forward returns are gross of costs; the signals
are already after the move (this study conditions on the signal, it does not predict the first move).

## 5. Open-source frameworks, LLM and Hermes

Tested on this host:

- **akshare** works and is the data layer: `stock_notice_report` (historical by date, all A shares),
  `news_cctv` (historical), `stock_info_global_em / _cls / _ths` (recent flashes), `stock_news_em` (per-stock recent),
  `stock_financial_analysis_indicator` (quarterly indicators). Eastmoney quote-list endpoints are blocked from this
  network; the announcement/news/financial endpoints work.
- **SnowNLP** was rejected: on "股东拟减持不超过2%股份" it returns 0.142 (mildly positive) and on "公司签订10亿元大单，
  业绩预增" it returns 0.025 (negative). Generic sentiment is not a finance classifier; `classify.py` uses a transparent
  finance lexicon instead.
- **LLM layer**: `llm_classify.py` reuses `quant/ml/llm/deepseek.py` and the existing prompt style. It degrades to a
  no-op when `DEEPSEEK_API_KEY` is absent; `--dry-run` was tested. A full run would send only the top policy/major-event
  headlines, not all 200k rows.
- **Hermes**: the repo's bridge is in `quant/ml/hermes/`; `digest.py` builds a daily prompt from the store. Hermes is
  not installed on this host, so the end-to-end agent run was not exercised. Example:
  `uv run python main.py --mode hermes-chat --prompt "$(uv run python research/news_selector/digest.py --date 2026-10-26)"`.

Heavier open-source options (FinBERT / FinGPT / Chinese financial BERT) were not installed: they need transformers and
model weights, and a five-month backtest needs a cheap first-stage classifier. The intended architecture is rules/LLM
only on the top candidates, not an LLM call per headline.

## 6. Reproduce

```bash
# 1. build the point-in-time universe (March 2026 liquidity)
uv run python research/news_selector/build_universe.py --asof 2026-03-01 --limit 800

# 2. fetch the local data (data/ is gitignored)
uv run --with akshare python research/news_selector/fetch_data.py --start 2026-05-01 --end 2026-10-26
uv run --with akshare python research/news_selector/fetch_fundamentals.py \
    --universe data/market/selector_universe_pit.csv --limit 800
uv run python scripts/news_archive.py --sources ths_limit_up --backfill-days 400
uv run --with akshare python research/news_selector/fetch_history.py \
    --start 2024-01-01 --end 2026-10-26 --workers 8 --sources ann,cctv
uv run python research/news_selector/policy_event_study.py --start 2024-01-01 --end 2026-10-26

# 3. run the selector and its ablations
uv run python research/news_selector/selector.py \
    --universe data/market/selector_universe_pit.csv --limit 800 \
    --start 2026-03-01 --test-start 2026-05-01 --end 2026-10-26
```

Output: `output/news_selector/summary.json`, `equity_<variant>.csv`, `trades_<variant>.csv` (gitignored).

## 7. Files and limitations

| File | Purpose |
|---|---|
| `../../quant/stock_data/news_store.py` | Data layer: SQLite tables and read/write methods |
| `fetch_data.py` | Announcements, CCTV policy news, limit-up reasons, recent market flashes |
| `fetch_history.py` | Long-window announcement/CCTV backfill with a thread pool (2024-2026) |
| `fetch_fundamentals.py` | Quarterly fundamentals for the liquid universe |
| `build_universe.py` | Point-in-time liquid universe |
| `classify.py` | Finance lexicon classifier (announcements, policy, limit-up reasons) |
| `selector.py` | Features, variants, portfolio backtest |
| `policy_event_study.py` | Section 4.1: conditional event study on 40,723 strong-dragon signals |
| `llm_classify.py` | Optional DeepSeek pass over selected headlines |
| `digest.py` | Daily Markdown digest for a Hermes session |
| `prompts/news_zh.md` | LLM prompt (strict JSON) |

Limitations: five-month window, 797 stocks with data, top-liquidity universe (no microcaps), announcement titles only
(no body), report-date + fixed publication lag rather than the actual announcement timestamp, current industry
membership (survivorship), coarse CCTV theme mapping, no intraday execution model, and data sources that can change.
The result is a research baseline, not a production strategy. `data/` is intentionally not in git; a future online
database can replace the `NewsStore` backend without changing the selector.
