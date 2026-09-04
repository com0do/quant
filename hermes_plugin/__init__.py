"""Quant Hermes plugin: registers 5 tools + 4 skills.

**This module runs inside the hermes-agent Python venv** (the hermes runtime of
`~/.hermes/profiles/quant/` after the user runs `hermes profile quant`). **Never import quant.\*** --
those packages live in the main quant venv (Py 3.12) and are not reachable from this venv (Py 3.11).

The real tool logic lives in `quant.ml.hermes.rpc` in the main repo; this module calls back into the
main venv via the subprocess `uv run python -m quant.ml.hermes.rpc <cmd> <json_args>`. The
QUANT_REPO_PATH environment variable points at the main repo root and is written to the profile's `.env`.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ENV = os.environ.get("QUANT_REPO_PATH", "")
_REPO = Path(_REPO_ENV).expanduser() if _REPO_ENV else None
_TIMEOUT_SEC = int(os.environ.get("QUANT_RPC_TIMEOUT_SEC", "120"))


def _call(cmd: str, args: dict[str, Any]) -> dict[str, Any]:
    if not _REPO or not _REPO.exists():
        return {
            "error": "QUANT_REPO_PATH not set or path missing",
            "hint": "add QUANT_REPO_PATH=/absolute/path/to/quant to the profile's .env",
            "value_seen": str(_REPO) if _REPO else "",
        }
    try:
        proc = subprocess.run(
            [
                "uv", "run", "python", "-m", "quant.ml.hermes.rpc",
                cmd, json.dumps(args, ensure_ascii=False),
            ],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"rpc timeout after {_TIMEOUT_SEC}s", "cmd": cmd}
    except FileNotFoundError:
        return {
            "error": "`uv` command not found on PATH",
            "hint": "install uv (https://docs.astral.sh/uv/) and ensure it's on hermes' PATH",
        }
    if proc.returncode != 0:
        return {
            "error": f"rpc failed (rc={proc.returncode})",
            "stderr": (proc.stderr or "")[:1000],
            "cmd": cmd,
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"bad json from rpc: {e}", "raw": (proc.stdout or "")[:1000]}


def _handle_get_kbars(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return _call("get_kbars", args or {})


def _handle_get_fundamentals(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return _call("get_fundamentals", args or {})


def _handle_get_announcements(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return _call("get_announcements", args or {})


def _handle_compute_factor(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return _call("compute_factor", args or {})


def _handle_run_backtest_slice(args: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return _call("run_backtest_slice", args or {})


_TOOLS = [
    ("get_kbars", _handle_get_kbars, "GET_KBARS"),
    ("get_fundamentals", _handle_get_fundamentals, "GET_FUNDAMENTALS"),
    ("get_announcements", _handle_get_announcements, "GET_ANNOUNCEMENTS"),
    ("compute_factor", _handle_compute_factor, "COMPUTE_FACTOR"),
    ("run_backtest_slice", _handle_run_backtest_slice, "RUN_BACKTEST_SLICE"),
]


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint (called by hermes at startup)."""
    from . import tool_schemas

    for name, handler, schema_attr in _TOOLS:
        try:
            ctx.register_tool(
                name=name,
                toolset="quant",
                schema=getattr(tool_schemas, schema_attr),
                handler=handler,
            )
        except Exception as e:  # noqa: BLE001 — do not block hermes startup on partial failure
            logger.warning("quant plugin: failed to register tool %s: %s", name, e)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.exists():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                try:
                    ctx.register_skill(child.name, skill_md)
                    logger.info("quant plugin: registered skill %s", child.name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("quant plugin: skill %s not registered: %s", child.name, e)
