# Machine Learning and AI Integration Research Plan

> **Current status (this update)**: the ML/AI layers in the repo are as follows.
>
> **Implemented (MVP, unverified)**:
>
> - **One-shot LLM call layer**: `quant/ml/llm/`. Calls the official DeepSeek API directly (OpenAI-compatible protocol).
>   Batch announcement JSONL → SQLite structured-signal pipeline. **Stateless, no tools, no memory**.
>   Entry point `main.py --mode nlp-analyze [--dry-run]`.
>
> - **Hermes agent integration**: split across two places
>   - `quant/ml/hermes/` — the bridge in the main quant venv (Py 3.12): the runner goes through the `hermes -z` CLI + profile; rpc is the endpoint the plugin calls back into
>   - `hermes_plugin/` — the Hermes plugin itself (runs in hermes's Py 3.11 venv): `plugin.yaml + __init__.py + tool_schemas + skills/`
>
>   It bridges the **Nous Research hermes-agent runtime** (not an LLM). The four AI roles = four standard agentskills.io skills:
>   `analyst_zoe / info_charles / risk_kris / trader_ethan`. Five quant tools (`get_kbars / get_fundamentals /
>   get_announcements / compute_factor / run_backtest_slice`) are registered into hermes through the plugin. DeepSeek is the model backend,
>   attached through the hermes profile config. The `hermes profile` mechanism provides isolation, leaving the user's existing profiles untouched.
>   Entry point `main.py --mode hermes-chat [--profile <name>] [--prompt "..." | --skill <name>]`.
>
> - **GBM factor aggregation**: `quant/ml/gbm/` + `quant/stock_strategy/gbm_multifactor.py`.
>   LightGBM is an optional dependency; when it is not installed or the model is missing, the strategy degrades to a neutral score. Entry point `main.py --mode gbm-train`.
>
> **Not implemented**: anomaly detection, standalone RAG, execution-layer RL, automatic financial-report PDF fetching.
>
> The **only pre-existing** ML component is still `quant/stock_strategy/online_learning.py` (EGD).
>
> If anything in the "evaluation/plan" parts of this document gets implemented, move that item from here into the corresponding strategy/execution document.

---

## 1. What Problems We Want ML/AI to Solve

Back to the ADR-3 principle: "**keep strategies simple, validate strictly, allow parameters to be fine-tuned online**".

ML/AI can only play an **auxiliary** role — it must not replace the main strategy logic or risk control.

Ordered by "low risk / high value", there are 4 possible entry points:

| # | Scenario | Value | Risk | Recommended priority |
|---|------|------|------|-----------|
| 1 | NLP structuring of financial reports/announcements | High | Low | ⭐⭐⭐ (do first) |
| 2 | Factor mining (GBM ranking) | Medium | Medium | ⭐⭐ (do second) |
| 3 | Anomaly detection (IsolationForest / Autoencoder) | Medium | Low | ⭐⭐ (do second) |
| 4 | Reinforcement learning (RL) for execution order slicing | Medium | High | ⭐ (long term) |

Things we **do not do**:

- **Do not** use RL for buy/sell decisions (sample efficiency, non-stationarity and interpretability are all inadequate).
- **Do not** use a CNN to read candlestick chart images directly (huge overfitting risk and not interpretable).
- **Do not** let an LLM output buy/sell signals directly (hallucination, strong prompt dependence, cannot be backtested).

## 2. Item-by-Item Evaluation

### 2.1 Reinforcement Learning (RL)

- **Common fantasy**: use PPO/A3C/DQN to learn a "stock-picking strategy" or "position management".
- **Real constraints**:
  - A-shares have one candlestick per day; one training run would need decades of samples, so sample efficiency is far from enough.
  - The market is non-stationary (regime shift); a strategy trained by RL stops working in the next regime.
  - The reward function is hard to design — equity gain? drawdown penalty? Sharpe ratio? Each of them induces odd behavior.
  - Not explainable: when the model errs, there is no way to find the root cause.
- **The only meaningful scenario**: **order execution slicing** (choosing TWAP/VWAP parameters) — there is a clear immediate reward (average fill price vs the VWAP benchmark) and it can converge within tens of thousands of samples.
- **Conclusion**: **no RL at the decision layer; the execution layer can be a long-term research project** (after Phase 3).

### 2.2 AI Agent (Hermes agent + DeepSeek as the Model Backend)

**Terminology clarification**: in this project, **Hermes specifically means [Nous Research hermes-agent](https://github.com/NousResearch/hermes-agent) (an agent runtime)**,
**not** a fine-tuned LLM such as Hermes-3. The real LLM is DeepSeek, attached through hermes's config.

- **Two reasonable roles**:
  1. **One-shot LLM call** (no agent) — read unstructured text, output structured JSON. Batch announcement structuring belongs here. Goes through `quant/ml/llm/`, calling DeepSeek directly.
  2. **Multi-step agent reasoning** (Hermes runtime) — an assistant that uses tools: look up quotes/announcements first, then synthesize, then produce a view. Goes through `quant/ml/hermes/`.
- **Typical tasks**:
  - One-shot: financial-report key-point extraction, announcement sentiment classification, risk-tag labeling → the LLM layer is enough
  - Multi-step: given a stock + date, have the agent compile a "fundamentals + events + price-volume" combined briefing → the Hermes agent layer
- **Implementation path (infrastructure already implemented)**:
  1. LLM layer: JSONL announcement input → DeepSeek structuring → `data/nlp/signals.db.nlp_signals` (`--mode nlp-analyze`)
  2. The Hermes plugin registers 5 tools (kbars / fundamentals / announcements / factors / backtest_slice)
  3. The four skills (`analyst_zoe / info_charles / risk_kris / trader_ethan`) are attached under the plugin as markdown
  4. `--mode hermes-chat` calls hermes-agent from the main venv through a subprocess bridge (because it requires Py 3.11, while the main project is on Py 3.12)
- **Risk control**:
  - A single-round LLM output must be strict JSON; on parse failure write neutral + raw_error (implemented in the LLM layer)
  - In agent scenarios: no buy/sell advice, no fabricated data, no calling tools beyond authority (hard constraints are already written into the skills)
  - Every document must be traceable to its source (keep `source_text_hash` as the primary key)
  - Periodic manual spot checks (≥ 10 documents/week)
- **Dependency cost**: DeepSeek costs ¥0.001-0.004 per 1,000 tokens; a 1,000-character announcement costs about ¥0.005. A multi-step Hermes agent task is estimated at ¥0.02-0.1.
- **Conclusion**: **this is the most valuable AI direction and the one to do first**. Goes into Phase 3.

### 2.3 RAG (Retrieval-Augmented Generation)

- **Has no standalone value**; it only serves as an underlying component in agent scenarios.
- **Typical use**: give hermes-agent a company's last 3 years of financial reports/announcements as context so it can do "period-over-period comparison" and "detect shifts in management's statements".
- **How to implement**: `data/documents_index/` (chroma or faiss vector store) + a simple retrieval wrapper.
- **What not to do**: do not use RAG to directly answer a muddled question like "should I buy stock XX today"; RAG output must still go through strict validation as factors/signals.
- **Conclusion**: **follows the agent implementation**, not a standalone project.

### 2.4 CNN + GBM Quantitative Stock Selection

A common community proposal is "use a CNN to extract candlestick/time-series features, then use a GBM (LightGBM/XGBoost) for the final ranking".

- **CNN part**:
  - **High risk**: poor interpretability, large room for overfitting, and the amount of A-share candlestick samples is still small for a CNN.
  - **Decision for this project**: **no CNN** (seriously conflicts with ADR-3).
- **GBM part**:
  - Relatively controllable — it is just "better multi-factor weighting". A GBM can capture non-linearity between factors and is stronger than z-score weighting.
  - **Can be done**: first build a `simple_3factor_gbm` baseline whose inputs are still those same factors and whose output is the GBM score, then run a head-to-head comparison against the linearly weighted version.
  - **Bar**: the GBM version must pass a walk-forward that is **at least as strict** as the linear version's, and improve out-of-sample Sharpe ratio by ≥ 20% to be worth replacing it.
- **Conclusion**:
  - **No CNN**.
  - **GBM is a research experiment as a candidate replacement for linear weighting**, but with lower priority than the NLP Agent.

### 2.5 Anomaly Detection

- **Scenario**: identify abnormal market days (flash crash, abnormal volume surge, constituents collectively pushed up at the close), as an extra input to risk control.
- **Methods**: Isolation Forest / LSTM-Autoencoder / One-Class SVM.
- **Not used as**: a buy/sell signal; only used as a "pause trading" trigger.
- **Implementation path**:
  1. Train an IsolationForest on historical data (one import from sklearn is enough)
  2. Run it once before the open each day; if "anomaly probability > threshold", the daemon goes flat for the day
  3. Coexists side by side with existing hard rules such as `crash_ret5_threshold`
- **Conclusion**: **medium priority**, part of Phase 3 alongside the NLP Agent.

## 3. Dependency Adoption Decisions

Before adopting any ML/AI component, ask yourself:

1. Can it run in a **separate venv/subprocess**? (to avoid polluting the main quant venv)
2. Is there an **offline-capable** alternative? (to avoid depending on a commercial API that may go down)
3. Can it **produce structured intermediate results** (JSON/parquet) so that the main pipeline can degrade to a no-AI version when "AI is absent"?

Only if all 3 answers are "yes" may a dependency be added to `pyproject.toml`.

Recommended adoption order (strictly one at a time, not in parallel):

1. `scikit-learn` (for the IsolationForest / LightGBM baseline)
2. `lightgbm` (GBM ranking candidate)
3. `chromadb` or `faiss-cpu` (RAG vector store)
4. Agent client (first local llama.cpp / Ollama, then consider cloud APIs)

**Explicitly not adopted**: `torch`, `tensorflow`, `keras`. Any component that needs them belongs to "after Phase 3" and requires a separate project proposal.

## 3-Supplement A. User Guide for the One-Shot LLM Call Layer (`quant/ml/llm/`)

**Positioning**: text in → structured JSON out. **Stateless, no tools, no memory**. The right answer for batch announcement analysis.

**Location**: `quant/ml/llm/deepseek.py` / `schema.py` / `announcement_pipeline.py` / `prompts/announcement_zh.md` / `README.md`.

**Dependency policy**: depends only on `requests`, which the repo already has; no extra package needs to be installed.

**Three steps to get started**:

1. Set `DEEPSEEK_API_KEY` in `.env` (you can copy the root `.env.example`).
2. Put the announcement JSONL in `data/nlp/inbox/*.jsonl`, one document per line, with fields `code / date / title / text`.
3. Run:
   - Smoke test: `uv run python main.py --mode nlp-analyze --dry-run`
   - Real run: `uv run python main.py --mode nlp-analyze`
   - Limit the count: `--limit 20`

**Output**: the `nlp_signals` table in `data/nlp/signals.db`; `source_text_hash` provides idempotency.

**Degradation modes**:

- Missing `DEEPSEEK_API_KEY` or the `/models` liveness probe fails → the whole batch returns immediately without writing to the DB.
- A single JSON parse failure → write a neutral record, keep the error in `raw_error`, and do not interrupt the main flow.

**Next validation step**: add `sentiment_score * confidence` as a new factor to `factor_analyze`; if IC/IR is significant, proceed to walk-forward.

## 3-Supplement B. User Guide for the Hermes Agent Integration (`quant/ml/hermes/` + `hermes_plugin/`)

**Positioning**: for scenarios that need multi-step reasoning, tools and memory. **Not** an LLM client.

**Layering** (the two venvs are strictly isolated and never import each other):

- **Main quant venv (Py 3.12) — `quant/ml/hermes/`**
  - `runner.py` — entry on the main-venv side, `chat()` / `run_skill()`; implemented as `subprocess.run(["hermes","-z",...], env={..., "HERMES_HOME": profile_dir})`
  - `rpc.py` — RPC endpoint on the main-venv side; the plugin calls back through `uv run python -m quant.ml.hermes.rpc <cmd> <args>`
  - `__init__.py` / `README.md`

- **Hermes venv (Py 3.11) — `hermes_plugin/` at the repo root**
  - `plugin.yaml` — manifest
  - `__init__.py` — `register(ctx)`: registers 5 tools + 4 skills (stdlib only)
  - `tool_schemas.py` — JSON schemas of the 5 tools
  - `skills/{analyst_zoe,info_charles,risk_kris,trader_ethan}/SKILL.md`
  - `configs/deepseek_profile.example.yaml` — reference snippet for the profile config.yaml
  - `setup.sh` — one-shot installation in profile mode
  - `README.md`

**Why two places**: hermes-agent requires Python 3.11, while the main quant project is on Python 3.12. **Code from the two venvs must not share one directory tree**, otherwise it is easy to import across them or install into the wrong one by mistake. The plugin logic lives at the repo root, and the main-venv bridge lives in `quant/ml/`.

**Why use a profile**: hermes supports multiple profiles (`~/.hermes/profiles/<name>/`), each with its own `config.yaml / .env / skills / plugins / memories`. Use a dedicated `quant` profile to hold this plugin, **without disturbing your existing default profile**. Switching relies on the `HERMES_HOME` environment variable (setup.sh also creates a `~/.local/bin/quant` wrapper alias along the way).

**Four steps to get started**:

1. Prerequisite: the `hermes` CLI is on PATH (`which hermes` prints something); DeepSeek is already configured in the default profile's `.env`
2. `bash hermes_plugin/setup.sh` — creates profile `quant` (`hermes profile quant --clone` inherits the DeepSeek key), symlinks the plugin, and writes `QUANT_REPO_PATH` into the profile `.env`
3. Enable the quant toolset:
   ```bash
   hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'
   ```
   or edit `~/.hermes/profiles/quant/config.yaml` manually, using `hermes_plugin/configs/deepseek_profile.example.yaml` as a reference
4. Run:
   - Free-form chat: `uv run python main.py --mode hermes-chat --prompt "..."`
   - Analyst Zoe: `uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01`
   - Intelligence officer Charles: `--skill info_charles --codes 000001.XSHE,600519.XSHG --date 2025-08-01`
   - Risk officer Kris: `--skill risk_kris --codes ...`
   - Trader Ethan: `--skill trader_ethan --code ... --date ...`
   - Switch profile: `--profile my_other_profile ...`

**Output**: `output/hermes_chat_last.json` (snapshot of the most recent session result).

**Degradation modes**:
- `hermes` CLI not found → `runner.chat()` returns `{"error": "hermes CLI not found on PATH", "hint": "..."}`
- profile directory missing / plugin symlink missing → returns `{"error": "hermes profile 'xxx' not set up", "hint": "bash hermes_plugin/setup.sh"}`
- RPC timeout → returns a structured error, which the agent relays to the user
- DB missing → the tool returns `error + hint`, and the skill has already taught the agent to relay it
- None of the above cases **affects the main quant pipeline** (backtest / walk-forward / live-auto, etc.)

**Division of labor among the four roles**:

| Role | Responsibility | Trigger words | Main tools |
|---|---|---|---|
| Analyst Zoe | In-depth fundamental interpretation | analysis / financial report / announcement | fundamentals + announcements + kbars |
| Intelligence officer Charles | Sentiment/event ranking | intelligence / sentiment / unusual movement | announcements + kbars |
| Risk officer Kris | Portfolio risk review | risk control / drawdown / stress test | kbars + announcements + (backtest) |
| Trader Ethan | Short-term price-volume structure | short-term / key levels / support-resistance | kbars |

## 3-Supplement C. User Guide for the GBM Factor Aggregation MVP

**Location**: `quant/ml/gbm/factor_gbm.py` (training + inference), `quant/stock_strategy/gbm_multifactor.py` (strategy).

**Dependency policy**: `lightgbm` is declared as an **optional dependency** (`[project.optional-dependencies].ml` in `pyproject.toml`). When it is not installed, the strategy degrades to a neutral score (all 0) and the main pipeline is unaffected.

**Install the dependency**: `uv sync --extra ml`

**Feature columns (aligned with `simple_3factor` to ease head-to-head comparison)**: `pe_ratio / pb_ratio / roe / ret_20 / ret_60 / vol_20`

**Label**: the **cross-sectionally demeaned** 5-day forward return, to avoid learning the common "market up/down" signal.

**Training**:

```bash
# Train on the database that data.runtime_csi1000_db_path in the config points to
uv run python main.py --mode gbm-train --config config/final_freeze_2025.toml

# Or specify explicitly
uv run python main.py --mode gbm-train \
  --db-path data/runtime/market_csi1000_latest.db \
  --start-date 2023-01-01 --end-date 2025-06-30 \
  --forward-days 5 \
  --model-path data/models/gbm_multifactor.txt
```

**Output**:

- `data/models/gbm_multifactor.txt`: LightGBM native format (not committed to git; `.gitignore` already excludes `data/models/`).
- `data/models/gbm_multifactor.metrics.json`: training/validation metrics (including rank IC, IR, feature importance).
- `output/gbm_train_summary.json`: summary of this training run (committed via the git whitelist).

**Inference**: add `"gbm_multifactor"` to `strategy_names` in `config/*.toml`, or use it directly to replace one of the multi-factor strategies for a comparison run.

**Validation bar** (same as the general bar in section 4 of `docs/02_strategy.md`):

1. `main.py --mode walk-forward` shows positive excess return in at least two of the three windows;
2. Mean out-of-sample rank IC > 0.03 and IR > 0.5;
3. Run a one-quarter head-to-head against `csi1000_enhanced`; out-of-sample Sharpe ratio must improve by ≥ 20% to be worth replacing it.

**Current status**: the MVP code runs, but there is no measured out-of-sample performance. Switching directly to production is not recommended.

## 4. Implementation Roadmap (aligned with the Phases of the evolution plan in section 1 of `docs/01_foundation.md`)

### Phase 3.1 (4-6 weeks): Anomaly Detection (low-risk start)

- Add the `sklearn` dependency
- Create `quant/tools/anomaly_detect.py`
- Train an IsolationForest on `market_features` (breadth, volatility, trading value)
- Run before the open each day, output to `data/runtime/anomaly_score.json`
- `quant/live/daemon.py` reads that file and forces a flat position if the anomaly score > threshold
- Validation: must cover at least two historical events, the 2020 pandemic and the 2024 Spring Festival small-cap crash

### Phase 3.2 (6-8 weeks): NLP + Hermes agent   **←  MVP implemented**

- **LLM layer (one-shot structured extraction) implemented**: DeepSeek direct client + JSONL→SQLite pipeline + idempotency + degradation (`quant/ml/llm/`)
- **Hermes agent layer implemented**: runner + RPC + plugin + 5 tools + 4 skills (`quant/ml/hermes/`)
- **To do**:
  - The user manually runs `bash quant/ml/hermes/setup.sh` to install hermes-agent + configure `~/.hermes/config.yaml`
  - Add a "JoinQuant announcement fetcher" that converts the structured `announcement` data periodically updated by JQ into JSONL
  - Load the `nlp_signals` table in `factor_analyze` and compute IC/IR
  - After it passes, add the significant fields (e.g. `sentiment_score * confidence`, one-hot of `risk_flags`) to the features of `simple_3factor` / `gbm_multifactor`
  - First real test of the 4 skills: using stocks whose announcements were loaded into the DB in the last 30 days, run each of the four skills once and manually review output quality

### Phase 3.2b: News and theme pipeline   **←  archive MVP implemented, tagging not implemented yet**

Motivation: in `research/strong_dragon/` (sections 5.1 and 5.2 of its README), company announcements, concept heat
and the "first limit-up of a new theme" did not separate winners from losers; most deck winners started on industry
or policy news and investor Q&A that no announcement contains, and none of those sources keeps a long history.

- **Implemented (MVP)**: `scripts/news_archive.py`, run daily by the home data node (`docs/09_home_data_node.md` §6):
  Tonghuashun limit-up pool with reasons (one year backfilled), Eastmoney limit-up pool, Eastmoney / Sina 7x24 flashes,
  Hudongyi investor Q&A, weekly Eastmoney concept-tag snapshots, all under `data/news/`.
- **Not implemented yet**:
  - DeepSeek tagging of each flash / answer through `quant/ml/llm/`: themes mapped to the concept taxonomy, first
    mention or repeat, strength, affected stocks; idempotent JSONL → SQLite like the announcement pipeline
  - Shanghai e-Interaction Q&A (only the Shenzhen Hudongyi is archived)
  - A theme-ignition / leader study on the archive once it covers 6+ months, judged walk-forward against the index

### Phase 3.3 (6-8 weeks): GBM Factor Aggregation   **←  MVP implemented**

- **Implemented**: `quant/ml/gbm/factor_gbm.py` + `quant/stock_strategy/gbm_multifactor.py`; lightgbm is an optional dependency
- **To do**:
  - Add a GBM training hyperparameter section to the config (`num_leaves`, `learning_rate`, `forward_days`)
  - Run a walk-forward head-to-head against `simple_3factor`
  - If it passes, consider training the NLP sentiment factor as an extra column as well (cross-phase dependency)

### Phase 3.4 (12+ weeks): RAG + Agent In-Depth Analysis

- Build a chroma vector store
- The agent supports "cross-period comparison" and "same-industry comparison"
- Output structured incremental signals

### Long term: Execution-Layer RL (Phase 4)

- Used only for choosing order-slicing parameters
- Separate subprocess, with training and inference separated
- Decoupled from the main strategy

## 5. Failure Criteria and Exit

If any project **meets any one of the following**, pause immediately and enter a post-mortem:

- After adding a dependency, `uv sync` of the main quant venv fails or CI turns red
- The output of an implemented component makes the `main.py --mode backtest` result non-reproducible (same seed, different results)
- At runtime the component uses more than 4GB of memory or delays the main pipeline by 10 minutes
- Cannot deliver a positive walk-forward result within 3 months

## 6. Code Organization Conventions (Implemented)

- Main-venv-side ML/AI components go into the `quant/ml/` subpackage, isolated from the main strategies; it currently contains:
  - `llm/` — one-shot LLM calls (no agent, no tools, no memory)
  - `hermes/` — the Hermes bridge (goes through the `hermes` CLI + profile, rpc endpoint)
  - `gbm/` — LightGBM factor aggregation
- **The cross-venv hermes plugin lives separately at the repo root `hermes_plugin/`** (runs in hermes's Py 3.11 venv). The code in the two places never imports each other.
- The interface to the main pipeline goes only through **SQLite / JSON files + subprocess**, with no in-process direct references (so the main pipeline is not dragged down at import time).
- Every component provides a "degradation mode":
  - LLM layer: DEEPSEEK_API_KEY missing / endpoint unreachable → whole batch returns early; single-record parse failure → neutral + `raw_error`
  - Hermes bridge: hermes not installed / profile not created / plugin not symlinked → returns `{"error": ..., "hint": ...}`
  - GBM model missing / lightgbm not installed → all-0 scores, and the main pipeline keeps running
- Binary assets (model weights, caches, vector stores) are all in `.gitignore` (`data/models/`, `data/hermes_cache/`, `data/nlp/`).
- **hermes-agent itself is not a pip dependency** (it requires Py 3.11, while our main project is on Py 3.12); it is attached through `bash hermes_plugin/setup.sh` under the user's existing `~/.hermes/` profile directory.

## 7. Relationship to Other Documents

- Consistent with Phase 3 of `docs/10_foundation/01_evolution_plan.md`; this document is a refinement of it.
- Consistent with the 4 reflection questions in `docs/10_foundation/04_quant_pros_cons.md`: any ML component must pass those 4 gates.
- Consistent with `docs/60_regulation/01_quant_regulation_playbook.md`: ML must not increase trading frequency, and can only raise the hit rate of each trade.

---

## 8. Update History

- Initial version: clarified the trade-offs among the four paths RL/Hermes/RAG/CNN+GBM. Currently there are no ML dependencies, only EGD online learning.
- 2026-08: separated the LLM layer from the Hermes agent layer; clarified that Hermes refers to the Nous Research hermes-agent runtime (not an LLM); DeepSeek as the unified model backend; the four skill skeletons landed.
- 2026-08 (refactor): moved the Hermes plugin from `quant/ml/hermes/plugin/` to the repo root `hermes_plugin/` (completely isolating the code of the Py 3.11 / 3.12 venvs); the runner now goes through the `hermes -z` CLI (`_agent_shim.py` deprecated), using `HERMES_HOME` + profile isolation, leaving the user's existing default profile untouched.
- 2026-10: Phase 3.2b news / theme archive MVP (`scripts/news_archive.py`, home data node); DeepSeek tagging of news not implemented yet.
