# AGENTS.md — quant repository navigation and working conventions

> Written for two kinds of readers:
>
> 1. **You (the repo owner)** — who wants to learn, run and iterate on this system quickly.
> 2. **AI assistants / future agents taking over** — who need to grasp the layout, conventions and safety red lines within minutes.
>
> Reading order: this file → `docs/00_INDEX.md` → `docs/01~90_*.md`.
>
> **Hard documentation rules**:
> - File names in English snake_case, content in English (git-diff friendly)
> - Merge documents on the same topic into a single `docs/NN_topic.md`; a two-digit numeric prefix enforces ordering
> - Documentation must match the facts in the code; assessment/plan content must explicitly be marked "not implemented yet" or "MVP, unverified"

---

## 0. TL;DR (one-minute version)

- This is an **A-share small-cap quant system**; the main battlefield is CSI 1000 / CSI 2000 / micro-caps.
- Data: **JoinQuant JQData** → local SQLite.
- Research: `main.py --mode backtest / walk-forward / optimize-fast / factor-analyze / gbm-train / nlp-analyze / ...`
- Live trading: `main.py --mode live-auto` (Linux strategy + Windows miniQMT, talking over an HTTP gateway)
- Frozen production config: `config/final_freeze_2025.toml`
- Strategy pool: 9 multi-factor / cross-sectional strategies (production uses `csi1000_enhanced`) + 1 GBM MVP (`gbm_multifactor`, unverified)
- ML/AI:
  - `quant/ml/llm/` — one-shot LLM calls (direct DeepSeek, `--mode nlp-analyze`)
  - `quant/ml/hermes/` — Hermes agent integration (Nous Research hermes-agent runtime; `--mode hermes-chat`)
  - `quant/ml/gbm/` — LightGBM factor aggregation (optional dependency)
- Core philosophy: **keep strategies simple, validate strictly, make parameters tunable online, GitHub Actions only orchestrates, live trading always runs locally**

---

## 1. Repository layout (one-screen overview)

```
quant/
├── main.py                     # unified entry point, pick a feature with --mode
├── pyproject.toml              # managed by uv; lightgbm is an optional dependency [ml]; ipykernel lives in the jupyter group (not installed by default; the group name dev is forbidden)
├── Makefile                    # Jupyter kernel (make jupyter / status / clean-venv); the kernel name comes from [project].name
├── README.md                   # English overview
├── AGENTS.md                   # ← the file you are reading now
├── TODO                        # open questions
├── .env.example                # copy to .env and fill in (Hermes / JQ / QMT etc. credentials)
├── config/                     # TOML configs (final_freeze_2025.toml is the frozen production version)
├── docs/                       # English docs (see docs/00_INDEX.md)
│   ├── 00_INDEX.md
│   ├── 01_foundation.md        # evolution + trading notes + risk control + pros and cons of quant
│   ├── 02_strategy.md          # cross-sectional strategies + price-volume psychology + pattern examples + school survey
│   ├── 03_engine.md            # engine parameters + frozen runbook + submission checklist
│   ├── 04_joinquant.md         # JoinQuant: 3 documents merged
│   ├── 05_live.md              # live trading / gateway: 6 documents merged
│   ├── 06_regulation.md        # response to tightening quant regulation
│   ├── 07_ai_research.md       # ML/AI integration (incl. Hermes/GBM MVP guide)
│   ├── 09_home_data_node.md    # home Linux data node deployment (JQ sync + R2 cold backup)
│   └── 90_reference.md         # reference material
├── quant/                      # main Python package
│   ├── app.py                  # application function for each --mode
│   ├── config.py               # AppConfig (Data / Strategy / Daemon / Risk / Gateway / Execution)
│   ├── backtest/               # backtest engine
│   ├── stock_data/             # SQLite / JQData client
│   ├── stock_filter/           # value pre-screening
│   ├── stock_exclusion/        # tradability filters
│   ├── stock_strategy/         # strategy implementations + score aggregation + lifecycle + EGD online learning
│   ├── execution/              # broker / paper / QMT HTTP / order splitting / risk control
│   ├── live/                   # pre-open scan / daemon / minute monitor / daily report / execution plan
│   ├── tools/                  # optimization / sync / factor analysis / walk-forward
│   ├── gateway/                # GatewayClient (Linux calling the Windows gateway)
│   ├── bridge/                 # JoinQuant → Redis signal schema
│   └── ml/                     # ML/AI integrations (all run in the main quant venv, Py 3.12)
│       ├── llm/                # one-shot LLM calls (direct DeepSeek + idempotent JSONL→SQLite pipeline)
│       ├── hermes/             # Hermes bridge (via the `hermes` CLI + profile; the paired plugin is in <repo>/hermes_plugin/)
│       └── gbm/                # LightGBM factor aggregation (optional dependency)
├── hermes_plugin/              # Hermes plugin (runs in the hermes venv, Py 3.11; symlinked to ~/.hermes/profiles/quant/plugins/quant/)
│   ├── plugin.yaml
│   ├── __init__.py             # register(ctx): registers 5 tools + 4 skills
│   ├── tool_schemas.py
│   ├── skills/{analyst_zoe,info_charles,risk_kris,trader_ethan}/SKILL.md
│   ├── configs/deepseek_profile.example.yaml
│   ├── setup.sh                # create profile + symlink plugin + write the profile .env
│   └── README.md
├── xtquant_gateway/            # Windows-side gateway service
├── deploy/
│   ├── gateway_server/         # standalone deployment environment for the Windows QMT gateway
│   └── home_data_node/         # Linux data node (systemd + rclone + R2 cold backup, see docs/09)
├── scripts/                    # operations scripts
├── research/                   # one-off studies with their own README (e.g. strong_dragon: deck audit + reverse-engineered signal + JoinQuant script)
├── test/                       # JoinQuant-side strategy scripts
├── output/                     # run artifacts (.gitignore, except whitelisted summary JSON)
├── data/                       # local data & models (.gitignore; symlinked to the big disk)
│   ├── runtime/*.db            # main market database
│   ├── models/                 # ML models (GBM training artifacts, generated dynamically)
│   ├── nlp/                    # NLP input JSONL + output signals.db
│   └── news/                   # daily news / theme archive (scripts/news_archive.py, run by the home data node)
├── .workflow/                  # Gitee Go pipelines (jq-signin.yml: daily JoinQuant check-in from Mainland-China runners)
└── .github/workflows/          # daily backtest drift, factor analysis, etc. CI (cannot reach JoinQuant: region-blocked)
```

---

## 2. Shortest path for "learn quant from scratch + get familiar with JoinQuant strategies"

Follow the steps in order: get started in a day, understand mainstream JoinQuant strategies in a week.

| Step | What to do | Where |
|----|------|------|
| 0 | Adjust your mindset: accept the 4 big limitations | `docs/01_foundation.md` §4 Advantages and Limitations of Quantitative Trading + §2/§3 |
| 1 | Master the cross-sectional strategy skeleton | `docs/02_strategy.md` §1; compare with `quant/stock_strategy/simple_3factor.py`, `jq_multifactor_clone.py` |
| 2 | Understand the "psychology → quant" translation | `docs/02_strategy.md` §2, §3 |
| 3 | Get familiar with the engine and parameters | `docs/03_engine.md` §1; run `--mode backtest` once |
| 4 | Judge whether a strategy is overfitted | `--mode walk-forward`, `--mode factor-analyze` |
| 5 | Port a JoinQuant strategy | `docs/04_joinquant.md` |
| 6 | Go live (paper trading for 1 week first) | `docs/05_live.md` §1, §2, §4, §6 |

---

## 3. Command cheat sheet

```bash
# Frozen backtest (the most common)
uv run python main.py --mode backtest --config config/final_freeze_2025.toml

# walk-forward validation
uv run python main.py --mode walk-forward --config config/final_freeze_2025.toml

# Factor IC/IR
uv run python main.py --mode factor-analyze --config config/final_freeze_2025.toml

# Fast optimization pipeline
uv run python main.py --mode optimize-fast --config config/final_freeze_2025.toml

# JoinQuant data sync
uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml

# Run .ipynb files in VS Code / Cursor: one command installs the jupyter group + registers a kernel bound to .venv (does not affect ordinary users or CI)
make jupyter

# Full paper-trading flow
uv run python main.py --mode live-auto --config config/live_paper.toml

# ← new: train the GBM factor-aggregation model (run uv sync --extra ml first)
uv run python main.py --mode gbm-train \
  --db-path data/runtime/market_csi1000_latest.db \
  --start-date 2023-01-01 --end-date 2025-06-30

# One-shot structured extraction from announcements via the LLM (direct DeepSeek)
uv run python main.py --mode nlp-analyze --dry-run       # smoke test
uv run python main.py --mode nlp-analyze                 # real run (needs DEEPSEEK_API_KEY)

# Multi-step Hermes agent chat (run bash hermes_plugin/setup.sh first to create the profile and symlink the plugin)
uv run python main.py --mode hermes-chat --prompt "Take a look at how 000001.XSHE has been doing lately"
uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --skill info_charles --codes 000001.XSHE,600519.XSHG
uv run python main.py --mode hermes-chat --skill risk_kris --codes 000001.XSHE,600519.XSHG
uv run python main.py --mode hermes-chat --skill trader_ethan --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --profile alt --prompt "..."   # switch profile for isolation
```

---

## 4. Key design decisions (cheat sheet)

From `docs/01_foundation.md` §1; hard constraints for day-to-day work:

- **ADR-1**: GitHub Actions only orchestrates (sync / drift / alerts); live trading always runs locally.
- **ADR-2**: miniQMT is the main path, with full-featured QMT as the fallback; the `xtquant_gateway` API stays the same.
- **ADR-3**: at most 3 core factors per strategy; it must pass walk-forward; fine-tune slowly with online learning.
- **ADR-4**: `data/` stays out of git; `data/models/` and `data/nlp/` are always generated dynamically.

---

## 5. Data, configuration, secrets (red lines)

- **`data/` (including `models/`, `nlp/`, `news/`, `hermes_cache/`), `output/*` (except whitelisted summaries) and `.env` stay out of git.** `uv.lock` **is committed**: all CI workflows run `uv sync --frozen`, so regenerate it with `uv lock` whenever `pyproject.toml` changes.
- JoinQuant account/password, `QMT_GATEWAY_TOKEN`, `DEEPSEEK_API_KEY` etc. go **only in `.env`** or GitHub Secrets. Never write them into code or config.
- LLM models stay out of git: they are only called via the DeepSeek API (paid); this repo only stores client code + prompt templates.
- **The hermes-agent runtime is installed and managed independently**: it lives in `~/.hermes/` (with a Py 3.11 venv) and this repo does not package it.
  Hermes-related credentials (DeepSeek key etc.) go in the hermes profile's `.env` (e.g. `~/.hermes/profiles/quant/.env`), **not** this repo's `.env`.
- **Do not put code that runs in the hermes venv into `quant/ml/hermes/`**: only the bridge on the main venv side belongs there. The real plugin is in **`hermes_plugin/` at the repo root**.
- LightGBM is an **optional dependency** (`pyproject.toml [project.optional-dependencies].ml`); when it is not installed the GBM strategy degrades to a neutral score.

---

## 6. Conventions when modifying the repo

1. **Do not write comments that explain what the code does.** Comments are only for explaining non-obvious trade-offs.
2. **Strategy parameter changes** go either in `config/*.toml` or in the defaults of `quant/config.py`. Never hard-code.
3. **New strategies** must be registered in `quant/stock_strategy/registry.py`.
4. **Backtest/optimization artifacts** are always written to `output/`; summary-type JSON is whitelisted into git.
5. **New gateway endpoints** require updating `docs/05_live.md` §2 and §3.
6. **ML components** go under `quant/ml/`, interfaces go through SQLite/JSON/HTTP, and a degraded mode is mandatory.
7. **Doc updates**: one `docs/NN_topic.md` per top-level topic; new documents take the next free number; existing numbers are never changed (unless they are renumbered in bulk and `AGENTS.md` is updated in sync).
8. **Docs aligned with the implementation**: once a feature is really implemented → move it from `07_ai_research.md` (or another planning section) into the main document; when code is deleted, clean up the related sections too.
9. **Self-check before committing**:
   - The backtest runs: `uv run python main.py --mode backtest --config config/final_freeze_2025.toml`
   - No new hard-coded paths or tokens were introduced
   - Sensitive files were not `git add`ed

---

## 7. Guide for AI assistants (for future agents)

- **Understand the goal**: make the system **run stably and reproducibly**, rather than piling on new features.
- **Backtest first**: any strategy change should ideally come with a before/after backtest comparison (`output/backtest_metrics.json`).
- **Read-only by default**: for anything that writes to disk / uses the network / places orders, give a plan first, then confirm, then execute. **Never switch to `broker_type = "qmt_http"` or remove `dry_run` without an explicit instruction.**
- **Respect data**: do not clean up / overwrite `data/`, `output/`, `.env`.
- **Docs follow code**: change the docs together with the code, especially `config/*.toml` field changes → `docs/03_engine.md` §1.
- **Language**: everything in this repo is written in English: docs, code comments, log/error messages, config comments and LLM prompts. Exceptions: (1) `test/` (strategy scripts copied from JoinQuant, not native to this repo); (2) functional Chinese literals that must match external Chinese text, namely JoinQuant server error messages and website UI strings, the Chinese risk-flag vocabulary in `quant/ml/llm/schema.py` and its prompt, and the Chinese trigger keywords of the Hermes skills. Keep an English comment next to every such literal explaining why it stays Chinese; (3) Chinese companion documents the owner asked for, named `*.zh.md` next to their English original (e.g. `research/strong_dragon/README.zh.md`); the English file stays the source of truth and both are updated together. Output-language requirements for an LLM (e.g. "respond in Chinese") are written in English.
- **Doc naming**: `docs/NN_topic.md`; do not use non-English file names; do not introduce new subdirectories.
- **ML/AI components**: prefer the existing infrastructure under `quant/ml/` (LLM layer / Hermes bridge / GBM trainer); models/weights never go into git.
- **Terminology**: in this project **Hermes specifically means the [Nous Research hermes-agent](https://github.com/NousResearch/hermes-agent) agent runtime**, not an LLM. The actual LLM is DeepSeek.
- **Never mix the two venvs**: `quant/ml/hermes/` only holds code that runs in the main quant venv (Py 3.12); `hermes_plugin/` (repo root) only holds plugin code that runs in the hermes venv (Py 3.11). The two sides talk via subprocess and must not import each other.
- **Do not overstate capabilities**: assessment documents must be marked "not implemented yet" or "MVP, unverified".

---

## 8. FAQ

| Question | Quick answer |
|------|---------|
| I want to run this on another machine; how do I initialize the data? | `git clone` → `uv sync` → `main.py --mode jq-bulk-sync` |
| What if the JoinQuant quota is used up? | The Gitee Go pipeline `.workflow/jq-signin.yml` checks in daily (credentials are Gitee Go general variables associated with the pipeline); the home `quant-daily.timer` does check-in + sync as well. GitHub runners cannot reach JoinQuant. |
| I want to reproduce a JoinQuant community strategy. | First check whether it is "cross-section + ranking + top-N"; if so, modify `simple_3factor.py` / `jq_multifactor_clone.py`. |
| What is the minimum to do before a strategy goes live? | walk-forward passes + 1 week of paper trading + gateway acceptance passes. |
| What if miniQMT is retired? | ADR-2 is prepared for it: switch to full-featured QMT; `xtquant_gateway` only changes how it connects. |
| I tuned the parameters hard and the backtest looks amazing? | Most likely overfitting. Run `--mode walk-forward` to check. |
| I want to add Turtle / Chan theory / deep learning. | `docs/02_strategy.md` §4 + `docs/07_ai_research.md` both have the decisions and thresholds. |
| My broker sent a risk warning / regulation is tightening. | The tightening parameter template in `docs/06_regulation.md`. |
| **How do I start batch structuring of announcements?** | Fill in `DEEPSEEK_API_KEY` in `.env`; put JSONL files into `data/nlp/inbox/`; run `main.py --mode nlp-analyze [--dry-run]`. |
| **How do I start the Hermes agent?** | With hermes installed → `bash hermes_plugin/setup.sh` (creates profile `quant`, symlinks the plugin, writes the profile .env) → `hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'` → `main.py --mode hermes-chat --skill analyst_zoe --code ...`. |
| **What are the four AI roles?** | `analyst_zoe` (fundamentals), `info_charles` (sentiment events), `risk_kris` (portfolio risk control), `trader_ethan` (short-term price-volume), all of them markdown files under `hermes_plugin/skills/`. |
| **Can I use a different profile?** | Yes. `main.py --mode hermes-chat --profile <name>`; during setup run `bash hermes_plugin/setup.sh <name>`. The default is `quant`. |
| **How do I train the GBM?** | `uv sync --extra ml` (installs lightgbm) → `main.py --mode gbm-train --db-path .../csi1000_latest.db`; the artifact is `data/models/gbm_multifactor.txt`. |
| Will the strategy crash if the GBM is untrained / lightgbm is not installed? | No, it degrades to a neutral score (all 0) and the main flow keeps running. |
| What happens if hermes is not installed / the profile was not created? | `--mode hermes-chat` returns `{"error": ..., "hint": ...}` with the next step; all other modes are completely unaffected. |

---

## 9. Related external resources

- JoinQuant API: <https://www.joinquant.com/help/api/help>
- JoinQuant JQData: <https://www.joinquant.com/help/api/doc?name=JQDatadoc&id=9842>
- Xuntou XtQuant / miniQMT: <https://dict.thinktrader.net/>
- DeepSeek API: <https://platform.deepseek.com>
- Nous Research hermes-agent: <https://hermes-agent.nousresearch.com/docs>
- Hermes profiles: <https://hermes-agent.nousresearch.com/docs/user-guide/profiles>
- agentskills.io specification: <https://agentskills.io/specification.md>
- LightGBM: <https://lightgbm.readthedocs.io>
- This project's GitHub remote: `git@github.com:com0do/quant.git`

---

> If something looks outdated or wrong, please update this `AGENTS.md` and the matching `docs/NN_*.md` together.
> **Code is the technique, docs are the way, risk control is life.**
