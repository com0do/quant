# quant/ml/hermes/ — Hermes agent 桥接（主 venv 侧）

**本目录里的代码只在主 quant venv（Py 3.12）里跑**。它是"跨到 hermes 世界"的入口。
真正跑在 hermes venv（Py 3.11）里的 plugin 代码在**仓库根的 `hermes_plugin/`**——那才是 Hermes plugin。

不要把两个 venv 的代码放同一棵目录树。

## 目录

```
quant/ml/hermes/
├── __init__.py     # 导出 runner 的公开 API
├── runner.py       # 用 `hermes -z` CLI 起 profile-scoped 会话
├── rpc.py          # RPC 端点：hermes_plugin 反向 subprocess 到这里
└── README.md       # 本文
```

对应的 hermes-venv-side 代码在：

```
<repo>/hermes_plugin/    # symlink 到 ~/.hermes/profiles/<profile>/plugins/quant/
├── plugin.yaml
├── __init__.py         # register(ctx)
├── tool_schemas.py
├── skills/*/SKILL.md
├── configs/
├── setup.sh
└── README.md
```

## 数据流

```
主 venv (Py 3.12)                              hermes venv (Py 3.11)
─────────────────                              ─────────────────
main.py --mode hermes-chat
  └─▶ quant.ml.hermes.runner.chat(prompt)
       └─▶ subprocess: `hermes -z PROMPT`      hermes gateway 起，加载 profile
             HERMES_HOME=~/.hermes/                  plugin: hermes_plugin/__init__.py
             profiles/quant/                              register(ctx)：注册 5 工具 + 4 skill
                                                    ↓
                                                    agent 决定调 get_kbars
                                                    ↓
                                                    tool handler:
                                                      subprocess `uv run python`
                                                      `-m quant.ml.hermes.rpc`
             ◀───── back in main venv ─────
             quant.ml.hermes.rpc.HANDLERS['get_kbars'](...)
               └─▶ sqlite3 读 data/runtime/*.db
               └─▶ json.dumps 打到 stdout
             ─────── to plugin handler ──────▶
                                                    handler 拿到 JSON 返回 hermes
                                                    ↓
                                                    agent 汇总最终回复
                                              ─── stdout ───▶
       runner 收到 stdout
       └─▶ 返回 {"response": ...}
```

## 起手

先在**仓库根**跑装 plugin：

```bash
bash hermes_plugin/setup.sh                # 建 profile "quant"，symlink plugin，写 .env
```

然后从主 venv 用：

```bash
uv run python main.py --mode hermes-chat --prompt "hi"
uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --profile alt_profile --prompt "..."   # 用别的 profile
```

## Runner 公开 API

```python
from quant.ml.hermes import chat, run_skill, is_installed, HermesInstallation, DEFAULT_PROFILE

chat(prompt, profile="quant", toolsets="core,quant", skill=None, timeout_sec=600, extra=None, model=None)
run_skill("analyst_zoe", code="000001.XSHE", date="2025-08-01", profile="quant")

is_installed(profile="quant")           # bool，只看 hermes CLI 是否可达
HermesInstallation.detect("quant").diagnose()   # 详细状态字典
```

## RPC 端点

```bash
uv run python -m quant.ml.hermes.rpc <cmd> <json_args>
```

5 个 `cmd`：`get_kbars / get_fundamentals / get_announcements / compute_factor / run_backtest_slice`。

每条 JSON 参数：
```bash
uv run python -m quant.ml.hermes.rpc get_kbars '{"code":"000001.XSHE","start_date":"2024-01-01","end_date":"2024-01-31"}'
```

失败时 stdout 打 `{"error": "..."}`，rc 非 0。

## 降级

- hermes CLI 不在 PATH → runner 返回 `{"error": "hermes CLI not found on PATH", "hint": "..."}`
- profile 目录缺失 / plugin symlink 缺失 → runner 返回 `{"error": "profile not set up", "hint": "bash hermes_plugin/setup.sh"}`
- RPC 端点某个 handler 里数据库缺失 → 返回结构化 error + hint，agent 转达用户

主 quant 链路（backtest / walk-forward / optimize / live-auto）不依赖 hermes——就算 hermes 全挂，那些都能跑。

## 相关文档

- 顶层导航：`AGENTS.md`
- 计划与决策：`docs/07_ai_research.md`
- LLM 一次性调用层：`quant/ml/llm/README.md`
- Plugin（hermes venv 侧）：`hermes_plugin/README.md`
- Hermes 官方：<https://hermes-agent.nousresearch.com/docs>
- Profile 手册：<https://hermes-agent.nousresearch.com/docs/user-guide/profiles>
