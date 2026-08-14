"""Hermes runner：从主 quant venv 起 hermes-agent 会话。

**只走 `hermes` CLI**，不 import `hermes_agent` Python 包（那个包在 hermes 自己的 Py 3.11
venv 里，主 venv 是 Py 3.12，import 会失败）。

实现思路：
    subprocess.run(
        ["hermes", "-z", prompt, "-t", "core,quant"],
        env={..., "HERMES_HOME": "~/.hermes/profiles/quant"},
    )

`hermes -z PROMPT` 是官方的 one-shot 模式，直接把最终回复打到 stdout，不进 TUI。

Profile 隔离：默认用 profile `quant`，通过 `HERMES_HOME` 环境变量指到 profile 目录。
用户装完 `bash hermes_plugin/setup.sh` 后 profile 就已经建好、plugin 已 symlink 好。
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
    hermes_bin: str | None                # hermes 可执行文件路径
    hermes_root: Path                     # ~/.hermes/
    profile: str                          # profile 名，例如 "quant"
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
        """诊断信息，用于 error 分支给用户清晰的下一步指令。"""
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
    """给 hermes subprocess 组一份 env。

    - HERMES_HOME 指到 profile 目录 —— 让 hermes 用该 profile 的 config/env/plugins
    - QUANT_REPO_PATH 默认从 profile .env 读；此处不注入，避免覆盖用户显式配置
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
    """跑一次 hermes 会话，返回 {"response": ...} 或 {"error": ..., "hint"?: ..., ...}。

    prompt：面向 agent 的任务描述
    profile：hermes profile 名（默认 "quant"）
    toolsets：逗号分隔的 toolset 列表（默认 "core,quant"）
    skill：可选，在 prompt 前拼一句 "[Use skill:quant:<skill>]"
    extra：附加 JSON 上下文，会拼到 prompt 末尾
    model：可选覆盖模型（provider/model 格式，例如 "deepseek/deepseek-chat"）
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
        parts.append("\n附加上下文（JSON）：\n" + json.dumps(extra, ensure_ascii=False, indent=2))
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
    "analyst_zoe": "以分析师 Zoe 的口吻，对下面的股票+日期做一份公告/财报深度解读。",
    "info_charles": "以情报官 Charles 的口吻，聚合近 30 日舆情+研报，给出关键异动列表。",
    "risk_kris": "以风控官 Kris 的口吻，评估下面持仓/股票的回撤风险与异常信号，给出仓位建议。",
    "trader_ethan": "以交易员 Ethan 的口吻，基于最近价量结构，给出日内/次日的操作要点（仅供研究，不构成建议）。",
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
    """以某个 skill 作为出发点跑一次会话。"""
    if skill not in SKILL_REGISTRY:
        return {"error": f"unknown skill: {skill}", "known": list(SKILL_REGISTRY.keys())}

    lines = [SKILL_REGISTRY[skill]]
    if code:
        lines.append(f"股票代码：{code}")
    if date:
        lines.append(f"截止日期：{date}")
    if start_date and end_date:
        lines.append(f"分析区间：{start_date} 到 {end_date}")
    if extra_prompt:
        lines.append(extra_prompt.strip())

    return chat(
        "\n".join(lines),
        profile=profile,
        skill=skill,
        timeout_sec=timeout_sec,
    )
