"""Hermes runner: start a hermes-agent session from the main quant venv.

**Uses the `hermes` CLI only**; never imports the `hermes_agent` Python package (that package lives
in hermes' own Py 3.11 venv, while the main venv is Py 3.12, so the import would fail).

Approach:
    subprocess.run(
        ["hermes", "-z", prompt, "-t", "core,quant"],
        env={..., "HERMES_HOME": "~/.hermes/profiles/quant"},
    )

`hermes -z PROMPT` is the official one-shot mode: it prints the final reply to stdout and never enters the TUI.

Profile isolation: the default profile is `quant`, selected by pointing the `HERMES_HOME` environment
variable at the profile directory. After the user runs `bash hermes_plugin/setup.sh`, the profile exists
and the plugin is already symlinked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROFILE = "quant"
DEFAULT_TOOLSETS = "core,quant"


@dataclass
class HermesInstallation:
    hermes_bin: str | None                # path to the hermes executable
    hermes_root: Path                     # ~/.hermes/
    profile: str                          # profile name, e.g. "quant"
    profile_dir: Path                     # ~/.hermes/profiles/<profile>/
    profile_env_file: Path                # ~/.hermes/profiles/<profile>/.env
    plugin_link: Path                     # ~/.hermes/profiles/<profile>/plugins/quant

    @classmethod
    def detect(cls, profile: str = DEFAULT_PROFILE) -> "HermesInstallation":
        root = Path(os.environ.get("HERMES_ROOT", str(Path.home() / ".hermes"))).expanduser()
        profile_dir = root / "profiles" / profile
        return cls(
            hermes_bin=shutil.which("hermes"),
            hermes_root=root,
            profile=profile,
            profile_dir=profile_dir,
            profile_env_file=profile_dir / ".env",
            plugin_link=profile_dir / "plugins" / "quant",
        )

    def is_installed(self) -> bool:
        return bool(self.hermes_bin)

    def profile_ready(self) -> bool:
        return self.profile_dir.is_dir() and self.plugin_link.exists()

    def diagnose(self) -> dict:
        """Diagnostics: give the user clear next steps in the error branch."""
        return {
            "hermes_bin": self.hermes_bin,
            "hermes_root": str(self.hermes_root),
            "profile": self.profile,
            "profile_dir": str(self.profile_dir),
            "profile_dir_exists": self.profile_dir.is_dir(),
            "plugin_link": str(self.plugin_link),
            "plugin_link_exists": self.plugin_link.exists(),
            "profile_env_file_exists": self.profile_env_file.exists(),
        }


def is_installed(profile: str = DEFAULT_PROFILE) -> bool:
    return HermesInstallation.detect(profile).is_installed()


def _build_env(inst: HermesInstallation) -> dict[str, str]:
    """Build the env for the hermes subprocess.

    - HERMES_HOME points at the profile directory, so hermes uses that profile's config/env/plugins
    - QUANT_REPO_PATH is read from the profile .env by default; it is not injected here so an
      explicit user setting is never overridden
    """
    env = dict(os.environ)
    env["HERMES_HOME"] = str(inst.profile_dir)
    return env


def chat(
    prompt: str,
    profile: str = DEFAULT_PROFILE,
    toolsets: str = DEFAULT_TOOLSETS,
    timeout_sec: int = 600,
    skill: str | None = None,
    extra: dict | None = None,
    model: str | None = None,
) -> dict:
    """Run one hermes session; return {"response": ...} or {"error": ..., "hint"?: ..., ...}.

    prompt: task description addressed to the agent
    profile: hermes profile name (default "quant")
    toolsets: comma-separated toolset list (default "core,quant")
    skill: optional; prepends one line "[Use skill:quant:<skill>]" to the prompt
    extra: additional JSON context, appended to the end of the prompt
    model: optional model override (provider/model format, e.g. "deepseek/deepseek-chat")
    """
    inst = HermesInstallation.detect(profile)
    if not inst.is_installed():
        return {
            "error": "hermes CLI not found on PATH",
            "hint": "install hermes-agent (curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash), then `bash hermes_plugin/setup.sh`",
            "diagnose": inst.diagnose(),
        }
    if not inst.profile_ready():
        return {
            "error": f"hermes profile '{profile}' not set up",
            "hint": f"run `bash hermes_plugin/setup.sh {profile}` to create the profile and link the plugin",
            "diagnose": inst.diagnose(),
        }

    parts: list[str] = []
    if skill:
        parts.append(f"[Use skill:quant:{skill}]")
    parts.append(prompt.strip())
    if extra:
        parts.append("\nAdditional context (JSON):\n" + json.dumps(extra, ensure_ascii=False, indent=2))
    final_prompt = "\n\n".join(parts)

    cmd: list[str] = [inst.hermes_bin or "hermes", "-z", final_prompt]
    if toolsets:
        cmd += ["-t", toolsets]
    if model:
        cmd += ["-m", model]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=_build_env(inst),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"hermes chat timeout after {timeout_sec}s", "profile": profile}

    if proc.returncode != 0:
        return {
            "error": f"hermes failed (rc={proc.returncode})",
            "stderr": (proc.stderr or "")[:2000],
            "cmd": cmd[:3] + ["<prompt>"] + cmd[4:],
            "profile": profile,
        }
    return {
        "response": (proc.stdout or "").strip(),
        "profile": profile,
        "toolsets": toolsets,
    }


SKILL_REGISTRY = {
    "analyst_zoe": "As analyst Zoe, write an in-depth reading of the announcements / financial reports for the stock and date below.",
    "info_charles": "As intelligence officer Charles, aggregate the last 30 days of sentiment and research reports and list the key anomalies.",
    "risk_kris": "As risk officer Kris, assess the drawdown risk and anomaly signals of the holdings/stocks below and give position-sizing advice.",
    "trader_ethan": "As trader Ethan, based on the recent price-volume structure, give intraday / next-day action points (research only, not investment advice).",
}


def run_skill(
    skill: str,
    code: str | None = None,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    extra_prompt: str | None = None,
    profile: str = DEFAULT_PROFILE,
    timeout_sec: int = 600,
) -> dict:
    """Run one session starting from a given skill."""
    if skill not in SKILL_REGISTRY:
        return {"error": f"unknown skill: {skill}", "known": list(SKILL_REGISTRY.keys())}

    lines = [SKILL_REGISTRY[skill]]
    if code:
        lines.append(f"Stock code: {code}")
    if date:
        lines.append(f"As-of date: {date}")
    if start_date and end_date:
        lines.append(f"Analysis window: {start_date} to {end_date}")
    if extra_prompt:
        lines.append(extra_prompt.strip())

    return chat(
        "\n".join(lines),
        profile=profile,
        skill=skill,
        timeout_sec=timeout_sec,
    )
