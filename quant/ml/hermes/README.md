# quant/ml/hermes/ — Hermes agent bridge (main-venv side)

**The code in this directory runs only in the main quant venv (Py 3.12)**. It is the entry point for "crossing into the hermes world".
The plugin code that actually runs in the hermes venv (Py 3.11) is in **`hermes_plugin/` at the repo root** — that is the real Hermes plugin.

Do not put code for the two venvs in the same directory tree.

## Directory layout

```
quant/ml/hermes/
├── __init__.py     # exports the runner's public API
├── runner.py       # starts a profile-scoped session via the `hermes -z` CLI
├── rpc.py          # RPC endpoint: hermes_plugin subprocesses back into here
└── README.md       # this file
```

The corresponding hermes-venv-side code is at:

```
<repo>/hermes_plugin/    # symlinked to ~/.hermes/profiles/<profile>/plugins/quant/
├── plugin.yaml
├── __init__.py         # register(ctx)
├── tool_schemas.py
├── skills/*/SKILL.md
├── configs/
├── setup.sh
└── README.md
```

## Data flow

```
Main venv (Py 3.12)                            hermes venv (Py 3.11)
─────────────────                              ─────────────────
main.py --mode hermes-chat
  └─▶ quant.ml.hermes.runner.chat(prompt)
       └─▶ subprocess: `hermes -z PROMPT`      hermes gateway starts, loads profile
             HERMES_HOME=~/.hermes/                  plugin: hermes_plugin/__init__.py
             profiles/quant/                              register(ctx): registers 5 tools + 4 skills
                                                    ↓
                                                    agent decides to call get_kbars
                                                    ↓
                                                    tool handler:
                                                      subprocess `uv run python`
                                                      `-m quant.ml.hermes.rpc`
             ◀───── back in main venv ─────
             quant.ml.hermes.rpc.HANDLERS['get_kbars'](...)
               └─▶ sqlite3 reads data/runtime/*.db
               └─▶ json.dumps prints to stdout
             ─────── to plugin handler ──────▶
                                                    handler gets the JSON and returns it to hermes
                                                    ↓
                                                    agent composes the final reply
                                              ─── stdout ───▶
       runner receives stdout
       └─▶ returns {"response": ...}
```

## Getting started

First install the plugin by running this at the **repo root**:

```bash
bash hermes_plugin/setup.sh                # creates profile "quant", symlinks the plugin, writes .env
```

Then use it from the main venv:

```bash
uv run python main.py --mode hermes-chat --prompt "hi"
uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --profile alt_profile --prompt "..."   # use a different profile
```

## Runner public API

```python
from quant.ml.hermes import chat, run_skill, is_installed, HermesInstallation, DEFAULT_PROFILE

chat(prompt, profile="quant", toolsets="core,quant", skill=None, timeout_sec=600, extra=None, model=None)
run_skill("analyst_zoe", code="000001.XSHE", date="2025-08-01", profile="quant")

is_installed(profile="quant")           # bool; only checks whether the hermes CLI is reachable
HermesInstallation.detect("quant").diagnose()   # detailed status dict
```

## RPC endpoints

```bash
uv run python -m quant.ml.hermes.rpc <cmd> <json_args>
```

The 5 `cmd` values: `get_kbars / get_fundamentals / get_announcements / compute_factor / run_backtest_slice`.

JSON arguments for each call:
```bash
uv run python -m quant.ml.hermes.rpc get_kbars '{"code":"000001.XSHE","start_date":"2024-01-01","end_date":"2024-01-31"}'
```

On failure, stdout prints `{"error": "..."}` and the rc is non-zero.

## Degradation

- hermes CLI not on PATH → runner returns `{"error": "hermes CLI not found on PATH", "hint": "..."}`
- profile directory missing / plugin symlink missing → runner returns `{"error": "profile not set up", "hint": "bash hermes_plugin/setup.sh"}`
- A database missing inside an RPC endpoint handler → returns a structured error + hint, which the agent relays to the user

The main quant pipeline (backtest / walk-forward / optimize / live-auto) does not depend on hermes — even if hermes is completely down, all of those still run.

## Related documents

- Top-level navigation: `AGENTS.md`
- Plans and decisions: `docs/07_ai_research.md`
- One-shot LLM call layer: `quant/ml/llm/README.md`
- Plugin (hermes-venv side): `hermes_plugin/README.md`
- Hermes official docs: <https://hermes-agent.nousresearch.com/docs>
- Profiles guide: <https://hermes-agent.nousresearch.com/docs/user-guide/profiles>
