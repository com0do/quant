

## 2026-03-27

- 局势
  目前全球局势混乱，中东战火不断，股市要赚钱就要做中长期，中长期看板块，看业绩，要做超跌反弹并有业绩支撑的逻辑，比如六氟磷酸锂套了你半年，最近连续涨停。屡创新高的股票，比如存储，完全不具有投资价值。 集中持仓，在低点位重仓，前几个持仓点位都是 近20%，


## 聪明的投资者

不要把技术分析孤立起来看。研究股票的大市，研究公司的经营情况，研究公司的产品，再看股票的走势图，特别还要注重交易量的变化，只有在这个基础上，技术分析才有意义。

要确定大市的走向，最重要的是每天要追踪股票指数的运动。如美国的道琼斯指数，
日本的 日经指数，香港的恒生指数，上海、深圳的综合指数等等



















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