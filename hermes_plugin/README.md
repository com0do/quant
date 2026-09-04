# hermes_plugin/

**This directory is the standalone release point for the Hermes plugin**. It is **not** part of the `quant` Python package — the
code in it runs in the hermes-agent Python venv (Py 3.11) and is not shared with the main quant venv (Py 3.12).

The install flow symlinks the whole directory into the hermes profile's `plugins/quant/`:

```
~/.hermes/profiles/quant/plugins/quant   ──symlink──►   <repo>/hermes_plugin
```

This also explains why the plugin lives at the repo root rather than under `quant/ml/hermes/` — **to avoid piling code for two different venvs
into the same directory tree**.

## What it provides

- **5 tools** (toolset id: `quant`)
  - `get_kbars` — daily OHLCV
  - `get_fundamentals` — fundamentals snapshot
  - `get_announcements` — LLM-structured announcement signals (from `data/nlp/signals.db`)
  - `compute_factor` — point-in-time factor value lookup
  - `run_backtest_slice` — single-strategy short-window backtest (slow; use sparingly)
- **4 skills** (agentskills.io standard)
  - `analyst_zoe/SKILL.md` — in-depth fundamentals interpretation
  - `info_charles/SKILL.md` — sentiment / event aggregation
  - `risk_kris/SKILL.md` — portfolio risk review
  - `trader_ethan/SKILL.md` — short-term price-volume structure

## Installation

```bash
bash hermes_plugin/setup.sh                # default profile name = quant
bash hermes_plugin/setup.sh my_profile     # custom profile name
```

setup.sh does three things:
1. `hermes profile <name> --clone` creates the profile (inherits your current profile's config and .env, DeepSeek key included)
2. `ln -s <repo>/hermes_plugin ~/.hermes/profiles/<name>/plugins/quant`
3. Appends `QUANT_REPO_PATH=<repo>` to the profile's `.env`

**What you need to do manually**: add `quant` to that profile's `enabled_toolsets`:

```bash
hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'
```

## Data flow

```
User
 └─▶ hermes -z "..." (Py 3.11 venv, HERMES_HOME=~/.hermes/profiles/quant)
      └─▶ Load plugins/quant/__init__.py → register(ctx)
      └─▶ Agent decides to call a quant tool
           └─▶ handler subprocess: `uv run python -m quant.ml.hermes.rpc <cmd> <args>`
                └─▶ Main quant venv (Py 3.12) runs rpc.py, reads SQLite / runs backtests
                └─▶ JSON goes back to the handler → back to the agent
      └─▶ Agent composes the final reply
 └─▶ User
```

**Key point**: the plugin's `__init__.py` uses only the stdlib (`os / json / subprocess / pathlib / logging`) and
does not import the main quant package — those packages live in another venv, so the import would fail.

## Directory layout

```
hermes_plugin/
├── plugin.yaml                # Hermes plugin manifest
├── __init__.py                # register(ctx): register tools + skills
├── tool_schemas.py            # JSON schemas for the 5 tools
├── skills/
│   ├── analyst_zoe/SKILL.md
│   ├── info_charles/SKILL.md
│   ├── risk_kris/SKILL.md
│   └── trader_ethan/SKILL.md
├── configs/
│   └── deepseek_profile.example.yaml  # reference snippet for the profile config.yaml
├── setup.sh                   # one-shot: create profile + symlink + patch .env
└── README.md                  # this file
```

## Skill authoring conventions

When writing a SKILL.md:

1. **One skill = one role**. Don't write a "generic assistant skill" — that is hermes's default behavior.
2. **Define the input contract explicitly**: list which parameters are required and how defaults are filled in.
3. **List the available tools**: so the agent knows what it can and cannot call.
4. **Write a "when to use this skill" section**: used by hermes's intent routing.
5. **Set hard constraints**: overstepping authority, fabricating data, and giving trading advice are the three red lines.
6. **English body text, English file paths**: consistent with the repo documentation convention.

## Reloading after changes

Hermes supports hot reload, but to be safe, after editing a skill / plugin:

```bash
hermes profile show quant       # confirm the plugin is still there
# or restart fully: kill the hermes gateway, then start it again
```

## FAQ

- **`error: QUANT_REPO_PATH not set`**: add a line `QUANT_REPO_PATH=<abs>` to the profile's `.env`.
- **`error: uv command not found`**: the hermes process did not inherit your PATH. Install uv into `~/.local/bin/`, or explicitly add `PATH` to the profile's `.env`.
- **RPC timeout**: increase `QUANT_RPC_TIMEOUT_SEC` (default 120). `run_backtest_slice` may need 300s.
- **DB not found**: first run `main.py --mode jq-bulk-sync` to pull data, or `--mode nlp-analyze` to generate the announcement DB.
- **Plugin not loaded**: run `hermes plugins list --profile quant` to see whether quant is listed; if not, check that the symlink points the right way and that the id in `plugin.yaml` is `quant`.
