# hermes_plugin/

**这个目录是 Hermes 插件的独立发布点**。它**不**是 `quant` Python 包的一部分——里面的
代码在 hermes-agent 的 Python venv 里跑（Py 3.11），跟主 quant venv（Py 3.12）不共享。

安装流程会把整个目录 symlink 到 hermes profile 的 `plugins/quant/`：

```
~/.hermes/profiles/quant/plugins/quant   ──symlink──►   <repo>/hermes_plugin
```

这也解释了为什么 plugin 放在仓库根、而不是 `quant/ml/hermes/` 下——**避免把两个不同 venv
的代码堆在同一棵目录树里**。

## 提供什么

- **5 个工具**（toolset id: `quant`）
  - `get_kbars` — 日线 OHLCV
  - `get_fundamentals` — 基本面快照
  - `get_announcements` — LLM 结构化公告信号（来自 `data/nlp/signals.db`）
  - `compute_factor` — 因子点值查询
  - `run_backtest_slice` — 单策略短窗回测（慢，节制使用）
- **4 个 skill**（agentskills.io 标准）
  - `analyst_zoe/SKILL.md` — 基本面深度解读
  - `info_charles/SKILL.md` — 舆情/事件聚合
  - `risk_kris/SKILL.md` — 组合风险审查
  - `trader_ethan/SKILL.md` — 短线价量结构

## 装法

```bash
bash hermes_plugin/setup.sh                # 默认 profile 名 = quant
bash hermes_plugin/setup.sh my_profile     # 自定义 profile 名
```

setup.sh 干三件事：
1. `hermes profile <name> --clone` 建 profile（继承你当前 profile 的 config 和 .env，DeepSeek key 一并带过来）
2. `ln -s <repo>/hermes_plugin ~/.hermes/profiles/<name>/plugins/quant`
3. 在 profile 的 `.env` 追加 `QUANT_REPO_PATH=<repo>`

**你需要手动做的**：把 `quant` 加到该 profile 的 `enabled_toolsets`：

```bash
hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'
```

## 数据流

```
User
 └─▶ hermes -z "..." (Py 3.11 venv, HERMES_HOME=~/.hermes/profiles/quant)
      └─▶ 加载 plugins/quant/__init__.py → register(ctx)
      └─▶ Agent 决定调 quant tool
           └─▶ handler subprocess: `uv run python -m quant.ml.hermes.rpc <cmd> <args>`
                └─▶ 主 quant venv (Py 3.12) 跑 rpc.py，读 SQLite / 跑回测
                └─▶ JSON 回到 handler → 回到 agent
      └─▶ Agent 组织最终回复
 └─▶ User
```

**关键**：plugin `__init__.py` 只用 stdlib（`os / json / subprocess / pathlib / logging`），
不 import 主 quant 包——那些包在另一个 venv 里，import 会失败。

## 目录

```
hermes_plugin/
├── plugin.yaml                # Hermes plugin manifest
├── __init__.py                # register(ctx): 注册工具 + skill
├── tool_schemas.py            # 5 个工具的 JSON schema
├── skills/
│   ├── analyst_zoe/SKILL.md
│   ├── info_charles/SKILL.md
│   ├── risk_kris/SKILL.md
│   └── trader_ethan/SKILL.md
├── configs/
│   └── deepseek_profile.example.yaml  # profile config.yaml 参考片段
├── setup.sh                   # 一键：建 profile + symlink + 注 .env
└── README.md                  # 本文
```

## Skill 作者公约

写 SKILL.md 时：

1. **一份 skill = 一个角色**。别写"通用 assistant skill"——那是 hermes 默认行为。
2. **明确输入契约**：列清楚需要什么参数、缺省怎么补。
3. **列出可用工具**：让 agent 明白它能调什么、不能调什么。
4. **写"何时使用本 skill"**：给 hermes 的意图路由用。
5. **给硬性约束**：越权/编造/给操作建议是三大高压线。
6. **中文正文，英文文件路径**：与仓库文档约定一致。

## 修改后重载

Hermes 支持热重载，但为稳妥起见改完 skill / plugin 后：

```bash
hermes profile show quant       # 确认 plugin 还在
# 或彻底重启：kill 掉 hermes gateway 后重开
```

## 常见问题

- **`error: QUANT_REPO_PATH not set`**：profile 的 `.env` 里加一行 `QUANT_REPO_PATH=<abs>`。
- **`error: uv command not found`**：hermes 的进程没继承你的 PATH。把 uv 装到 `~/.local/bin/`，或在 profile 的 `.env` 里显式加 `PATH`。
- **RPC 超时**：调大 `QUANT_RPC_TIMEOUT_SEC`（默认 120）。`run_backtest_slice` 可能需要 300s。
- **DB 找不到**：先跑 `main.py --mode jq-bulk-sync` 拉数据，或 `--mode nlp-analyze` 生成公告库。
- **plugin 未加载**：`hermes plugins list --profile quant` 看是否列出 quant；如果没有，检查 symlink 是否指对方向、`plugin.yaml` 里的 id 是否为 `quant`。
