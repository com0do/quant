"""Hermes agent 集成层（跑在**主 quant venv, Py 3.12**）。

**核心事实**：
- hermes-agent 是 Nous Research 出的 agent runtime，不是 LLM。LLM 后端接 DeepSeek。
- hermes 装在 `~/.hermes/`（含独立 Py 3.11 venv），本层通过 `hermes` CLI subprocess 走 profile 模式接触它。
- 真正跑在 hermes venv 里的 plugin 代码在**仓库根的 `hermes_plugin/`**（不在本目录内），
  由 `hermes_plugin/setup.sh` symlink 到 `~/.hermes/profiles/<profile>/plugins/quant/`。

**本目录只放主 quant venv 里的东西**：
- `runner.py` — 用 `hermes -z PROMPT` 起会话，`HERMES_HOME` 隔离 profile
- `rpc.py` — RPC 端点，hermes_plugin 反向 subprocess 调这里读 SQLite / 跑回测
- `__init__.py` — 本文，导出 runner API

**使用**：
    uv run python main.py --mode hermes-chat --prompt "分析 000001.XSHE 最近的公告"
    uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
    uv run python main.py --mode hermes-chat --profile my_profile --prompt "..."

**未安装 hermes / profile 未 setup**：`runner.chat()` 会返回 `{"error": ..., "hint": "..."}`，主流程不中断。
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
