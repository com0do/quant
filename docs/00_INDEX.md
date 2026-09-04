# Documentation Index

> All closely related documents have been merged into single files; the numeric prefix enforces ordering, file names are English (git diff friendly), and the content is in English.
> Every file carries its own table of contents, so you can jump straight to the section you care about.

| # | File | Content | When to read |
|---|------|------|-----------|
| 1 | `01_foundation.md` | System evolution + trading notes + risk-control iron rules + pros and cons of quant + **strategy elegy and evolution** (§5) | **Must-read for getting started** |
| 2 | `02_strategy.md` | Cross-sectional strategy handbook + price-volume psychology + chart-pattern examples + overview of schools of thought | Must-read for learning quant and reading JoinQuant strategies |
| 3 | `03_engine.md` | Engine and parameters + frozen-config run handbook + pre-commit checklist | Read when tuning parameters, running backtests, or before committing |
| 4 | `04_joinquant.md` | JQ-to-QMT roadmap + data dictionary + Redis signal bridge + callback timing | Read when reusing JoinQuant data/strategies |
| 5 | `05_live.md` | Live-trading pipeline + QMT gateway protocol + xtquant V2 + deployment + xtdata + QKA | Read when going live or doing operations |
| 6 | `06_regulation.md` | Response playbook for tightening when quant share exceeds 25% | Read under regulatory pressure / when reducing trading frequency |
| 7 | `07_ai_research.md` | Evaluation and rollout roadmap for RL/Agent/RAG/GBM (including the current Hermes/GBM MVP status) | Must-read before discussing introducing ML/AI |
| 8 | `08_wufu_strategy.md` | Wufu fusion strategy in detail (S1 bottom-fishing + S2 rotation + peak tracking + backtest insights) | Must-read before iterating on wufu.py |
| 9 | `09_home_data_node.md` | Home Linux data node deployment (daily JQ sync + Cloudflare R2 cold backup + CI pull) | Read when setting up the primary data source / wiring up CI |
| 90 | `90_reference.md` | Session history + watchlist stock notes | Read when you need historical context |

## Recommended Reading Paths

- **Learning quant from scratch**: `01` (section 4: pros and cons of quant) → `02` → `03`
- **Understanding JoinQuant strategies and reusing JoinQuant resources**: `02` (sections 1, 4) → `04`
- **Understanding the wufu fusion strategy**: `02` (sections 1, 4) → `04` (section 4 callback timing) → `08`
- **Understanding this repo's current architecture**: `01` (section 1 evolution) → `03` → `05` (sections 1, 2)
- **Preparing to go live**: `01` (section 3 risk control) → `03` → `05` (1→2→4→6)
- **Adjusting strategies under tightening regulation**: `06` → `03` → `05` (section 3 xtquant V2)
- **Deciding whether to introduce ML/AI**: `01` (section 4 pros and cons) → `07`

## Naming and Update Conventions

1. One file per top-level topic, numbered `01~09`; `90` is reserved for reference material.
2. File names are English snake_case; the content is in English.
3. Keep a "table of contents" anchor table at the top of every file; update it whenever you add a new section.
4. Documents must stay aligned with the code facts; evaluation/plan content must be explicitly marked "not yet implemented" or "MVP pending validation".
5. Once a feature has really landed, move it from `07_ai_research.md` (or other planning sections) to the corresponding place in the main docs.
