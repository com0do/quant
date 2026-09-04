"""Hermes agent integration layer (runs in the **main quant venv, Py 3.12**).

**Key facts**:
- hermes-agent is an agent runtime from Nous Research, not an LLM. The LLM backend is DeepSeek.
- hermes is installed in `~/.hermes/` (with its own Py 3.11 venv); this layer reaches it through the `hermes` CLI as a subprocess in profile mode.
- The plugin code that actually runs inside the hermes venv lives in **`hermes_plugin/` at the repo root** (not in this directory);
  `hermes_plugin/setup.sh` symlinks it to `~/.hermes/profiles/<profile>/plugins/quant/`.

**This directory only contains what runs in the main quant venv**:
- `runner.py` — starts a session with `hermes -z PROMPT`, isolating the profile via `HERMES_HOME`
- `rpc.py` — RPC endpoints; hermes_plugin calls back here via subprocess to read SQLite / run backtests
- `__init__.py` — this file, exports the runner API

**Usage**:
    uv run python main.py --mode hermes-chat --prompt "Analyze the recent announcements of 000001.XSHE"
    uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
    uv run python main.py --mode hermes-chat --profile my_profile --prompt "..."

**hermes not installed / profile not set up**: `runner.chat()` returns `{"error": ..., "hint": "..."}` and the main flow is not interrupted.
"""

from quant.ml.hermes.runner import (
    DEFAULT_PROFILE,
    DEFAULT_TOOLSETS,
    HermesInstallation,
    SKILL_REGISTRY,
    chat,
    is_installed,
    run_skill,
)

__all__ = [
    "DEFAULT_PROFILE",
    "DEFAULT_TOOLSETS",
    "HermesInstallation",
    "SKILL_REGISTRY",
    "chat",
    "is_installed",
    "run_skill",
]
