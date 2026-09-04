# IX. Reference Material

> This file merges 2 reference documents: the session history summary and the watchlist stock notes.

## Table of Contents

- [1. Session History Summary](#1-session-history-summary)
- [2. Watchlist Stock Notes](#2-watchlist-stock-notes)

---

<a id="1-session-history-summary"></a>
# Quant Project Session History Summary

## 1. Business Goals

- Build a small-cap quantitative trading system for A-shares.
- Main battlefield: CSI 1000 / micro-cap.
- Workflow:
  - Backtest first
  - Then connect to QMT for live trading
  - Keep accumulating live-trading data for future evaluation/optimization

## 2. Historical Functional Requirements (Merged)

- Modular architecture:
  - `stock_filter`, `stock_exclusion`, `stock_strategy`, `stock_data`, `backtest`, `execution`, `live`, `tools`
- Data:
  - Integrate the local JoinQuant SDK (`jqdatasdk`)
  - Use SQLite as the primary store (replacing the early pickle workflow)
  - Incremental sync + resume from checkpoint
  - Separate one-year databases for CSI1000 / CSI2000
- Strategy:
  - Parallel scoring across multiple strategies + weighted aggregation
  - Separate buy and sell logic
  - D+2 holding constraint + stop-loss + trailing stop-loss
- Optimization:
  - Grid / iterative optimization
  - Hard constraints (`excess_ann_return > 0`, drawdown protection)
  - stage1 + stage2 use vectorbt pre-screening
  - Multi-process parallelism
- Live execution:
  - QMT mock + real adapter skeleton
  - TWAP/VWAP slicing
  - Order-timeout cancel/re-submit + passive/aggressive price chasing
  - Exposure/liquidity constraints
  - Daemon monitoring + daily report + archiving
- Intraday:
  - Minute-level sell monitoring merged with the daily-frequency sell score

## 3. Current Data Status (Snapshot)

- `data/market_cache_csi1000_1y.db`
  - Daily bars are fully populated
  - Fundamentals/factor snapshots are largely complete
- `data/market_cache_csi2000_1y.db`
  - Daily bars are fully populated
  - Snapshots not fully synced because of the JoinQuant quota window

## 4. Incident Log

- The source code under `quant/` was accidentally lost from the workspace.
- Emergency recovery relied on loader stubs + pyc.
- This state cannot be maintained long-term.
- The current recovery goal is to bring the project back to a maintainable pure-source state.

## 5. Recovery Goals for This Phase

- Replace all “Recovered loader stub” files with normal Python source.
- Preserve functional equivalence with the historical requirements above.
- Specifically preserve:
  - Iterative optimization with vectorbt stage1 + stage2 pre-screening
  - Multi-threaded/multi-process optimization paths
  - Live daemon + QMT mock execution loop
  - Evaluation workflow for the JoinQuant trial window

## 6. Verification Goals After the Rewrite

- Backtest:
  - `scripts/backtest_csi1000_baseline.py`
- Optimization:
  - `scripts/optimize_csi1000_baseline.py`
  - `quant.tools.iterative_optimize.run_iterative_optimize(...)`
- Live-trading smoke test:
  - `quant.live.daemon` single-cycle dry run
- Sync smoke test:
  - `scripts/jq_bulk_sync_smallcap.py` startup + resume-from-checkpoint path
  - If the quota is exhausted, mark it as an external blocker (not a code problem)

---

<a id="2-watchlist-stock-notes"></a>



## 2026-03-27

- Situation
  The global situation is currently chaotic, with war in the Middle East ongoing. To make money in the stock market you have to think medium-to-long term: look at sectors, look at earnings, and go for oversold rebounds backed by earnings. For example, lithium hexafluorophosphate had you trapped for half a year and has recently hit limit-up several days in a row. Stocks that keep making new highs, such as memory storage, have no investment value at all. Concentrate holdings and size up heavily at low price points; the first few entry levels were all close to 20%,


## The Smart Investor

Don't look at technical analysis in isolation. Study the broad market, study the company's operations, study the company's products, and only then look at the stock's price chart, paying particular attention to changes in trading volume; only on this basis does technical analysis become meaningful.

To determine the direction of the broad market, the most important thing is to track the movement of stock indices every day, such as the Dow Jones index in the US,
the Nikkei index in Japan, the Hang Seng index in Hong Kong, the Shanghai and Shenzhen composite indices, and so on



















```python
OPT_SAFE_MAX_WORKERS=16 uv run python - <<'PY'
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from copy import deepcopy
from datetime import datetime
import json, os
import pandas as pd
from quant.config import load_config
from quant.backtest.engine import run_backtest


def run_full(payload: tuple) -> dict:
    combo, cfg = payload
    exposure, gate_on, req_ma200, entry, sell_thr, stop_loss, trailing, top_k = combo
    c = deepcopy(cfg)
    c.strategy.strategy_names = ['csi1000_enhanced']
    c.strategy.strategy_weights = [1.0]
    c.strategy.dual_layer_enable = False
    c.strategy.max_total_exposure_pct = float(exposure)
    c.strategy.market_regime_gate_enable = bool(gate_on)
    c.strategy.market_regime_min_win_prob = 0.55
    c.strategy.require_above_ma200 = bool(req_ma200)
    c.strategy.allow_short_below_ma200_exception = True
    c.strategy.ma200_exception_min_ret5 = 0.03
    c.strategy.ma200_exception_vol_ratio_max = 3.2
    c.strategy.entry_interval_days = int(entry)
    c.strategy.buy_score_threshold = 0.0
    c.strategy.sell_score_threshold = float(sell_thr)
    c.strategy.stop_loss_pct = float(stop_loss)
    c.strategy.trailing_stop_pct = float(trailing)
    c.strategy.top_k = int(top_k)
    c.strategy.max_positions = int(top_k)
    c.strategy.value_filter_strict = False
    m = run_backtest(c, output_prefix='iter2_full', write_outputs=False).metrics
    return {
        'max_total_exposure_pct': exposure,
        'market_regime_gate_enable': gate_on,
        'require_above_ma200': req_ma200,
        'entry_interval_days': entry,
        'sell_score_threshold': sell_thr,
        'stop_loss_pct': stop_loss,
        'trailing_stop_pct': trailing,
        'top_k': top_k,
        **m,
    }


def run_robust(payload: tuple) -> dict:
    row, cfg = payload
    c = deepcopy(cfg)
    c.strategy.strategy_names = ['csi1000_enhanced']
    c.strategy.strategy_weights = [1.0]
    c.strategy.dual_layer_enable = False
    c.strategy.max_total_exposure_pct = float(row['max_total_exposure_pct'])
    c.strategy.market_regime_gate_enable = bool(row['market_regime_gate_enable'])
    c.strategy.market_regime_min_win_prob = 0.55
    c.strategy.require_above_ma200 = bool(row['require_above_ma200'])
    c.strategy.allow_short_below_ma200_exception = True
    c.strategy.ma200_exception_min_ret5 = 0.03
    c.strategy.ma200_exception_vol_ratio_max = 3.2
    c.strategy.entry_interval_days = int(row['entry_interval_days'])
    c.strategy.buy_score_threshold = 0.0
    c.strategy.sell_score_threshold = float(row['sell_score_threshold'])
    c.strategy.stop_loss_pct = float(row['stop_loss_pct'])
    c.strategy.trailing_stop_pct = float(row['trailing_stop_pct'])
    c.strategy.top_k = int(row['top_k'])
    c.strategy.max_positions = int(row['top_k'])
    c.strategy.value_filter_strict = False

    windows = [
        ('w1', '2025-01-01', '2025-04-30'),
        ('w2', '2025-05-01', '2025-08-31'),
        ('w3', '2025-09-01', '2025-12-23'),
    ]
    exs = []
    anns = []
    mdds = []
    for _, s, e in windows:
        cw = deepcopy(c)
        cw.data.start_date = s
        cw.data.end_date = e
        m = run_backtest(cw, output_prefix='iter2_rob', write_outputs=False).metrics
        exs.append(float(m['excess_ann_return']))
        anns.append(float(m['ann_return']))
        mdds.append(float(m['max_drawdown']))

    min_ex = min(exs)
    avg_ex = sum(exs) / len(exs)
    avg_ann = sum(anns) / len(anns)
    worst_mdd = min(mdds)
    robust_score = float(100.0 * row['excess_ann_return'] + 60.0 * min_ex + 25.0 * avg_ex + 10.0 * avg_ann + 2.0 * row['sharpe'] + 2.0 * worst_mdd)
    out = dict(row)
    out.update({
        'win_w1_excess': exs[0],
        'win_w2_excess': exs[1],
        'win_w3_excess': exs[2],
        'win_min_excess': min_ex,
        'win_avg_excess': avg_ex,
        'win_avg_ann': avg_ann,
        'win_worst_mdd': worst_mdd,
        'robust_score': robust_score,
    })
    return out

base = load_config(config_path='config/final_freeze_2025.toml')
combos = list(product(
    [0.75, 0.80, 0.90, 1.00],
    [False, True],
    [False, True],
    [1, 2],
    [0.88, 0.90, 0.94, 0.98],
    [0.05, 0.06],
    [0.10, 0.12],
    [3],
))
print(f'[iter2-stage1] combos={len(combos)}, workers=16')
with ProcessPoolExecutor(max_workers=16) as ex:
    rows = list(ex.map(run_full, [(c, base) for c in combos], chunksize=2))

full = pd.DataFrame(rows)
full = full.sort_values(['ann_return','excess_ann_return','sharpe'], ascending=False).reset_index(drop=True)
pos = full[full['excess_ann_return'] > 0].copy()

if pos.empty:
    top = full.head(30).copy()
else:
    top = pos.head(40).copy()

print(f'[iter2-stage2] candidates={len(top)}')
with ProcessPoolExecutor(max_workers=16) as ex:
    robust_rows = list(ex.map(run_robust, [(r._asdict(), base) for r in top.itertuples(index=False)], chunksize=1))

robust = pd.DataFrame(robust_rows).sort_values(['robust_score','excess_ann_return','ann_return'], ascending=False).reset_index(drop=True)

os.makedirs('output', exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
full_csv = f'output/iter2_full_scan_{ts}.csv'
robust_csv = f'output/iter2_robust_scan_{ts}.csv'
summary_json = f'output/iter2_summary_{ts}.json'
full.to_csv(full_csv, index=False)
robust.to_csv(robust_csv, index=False)

best_return = full.iloc[0].to_dict() if not full.empty else {}
best_excess = pos.sort_values(['excess_ann_return','ann_return','sharpe'], ascending=False).iloc[0].to_dict() if not pos.empty else {}
best_robust = robust.iloc[0].to_dict() if not robust.empty else {}
with open(summary_json, 'w', encoding='utf-8') as f:
    json.dump({
        'best_return': best_return,
        'best_excess': best_excess,
        'best_robust': best_robust,
        'positive_excess_count': int((full['excess_ann_return'] > 0).sum()),
        'total': int(len(full)),
        'files': {
            'full': full_csv,
            'robust': robust_csv,
        },
    }, f, ensure_ascii=False, indent=2)

print('[iter2] top return')
print(full[['max_total_exposure_pct','market_regime_gate_enable','require_above_ma200','entry_interval_days','sell_score_threshold','stop_loss_pct','trailing_stop_pct','ann_return','bench_ann_return','excess_ann_return','max_drawdown','sharpe']].head(10).to_string(index=False))
print('[iter2] top robust')
print(robust[['max_total_exposure_pct','market_regime_gate_enable','require_above_ma200','entry_interval_days','sell_score_threshold','stop_loss_pct','trailing_stop_pct','ann_return','excess_ann_return','win_w1_excess','win_w2_excess','win_w3_excess','win_min_excess','robust_score']].head(10).to_string(index=False))
print('[iter2] positive_excess=', int((full['excess_ann_return'] > 0).sum()), '/', len(full))
PY
```