# AGENTS.md — quant 仓库导航与工作约定

> 面向两类读者：
>
> 1. **你（仓库主人）** —— 想快速把这套系统学起来、跑起来、迭代起来。
> 2. **AI 助手 / 未来接手的 agent** —— 需要在几分钟内摸清脉络、约定与安全红线。
>
> 阅读顺序：本文 → `docs/00_INDEX.md` → `docs/01~90_*.md`。
>
> **文档硬规则**：
> - 文件名英文 snake_case，内容中文（git diff 友好）
> - 相同主题合并成一份 `docs/NN_topic.md`，两位数字前缀强制排序
> - 文档必须与代码事实对齐；评估/计划类内容显式标注"尚未实现"或"MVP 待验证"

---

## 0. TL;DR（一分钟版）

- 这是一个 **A 股小盘量化系统**，主战场中证 1000 / 中证 2000 / 小微盘。
- 数据：**聚宽（JoinQuant）JQData** → 本地 SQLite。
- 研究：`main.py --mode backtest / walk-forward / optimize-fast / factor-analyze / gbm-train / nlp-analyze / ...`
- 实盘：`main.py --mode live-auto`（Linux 策略 + Windows miniQMT，通过 HTTP 网关沟通）
- 冻结生产配置：`config/final_freeze_2025.toml`
- 策略池：9 个多因子/截面策略（生产用 `csi1000_enhanced`）+ 1 个 GBM MVP（`gbm_multifactor`，未验证）
- ML/AI：
  - `quant/ml/llm/` — LLM 一次性调用（DeepSeek 直连，`--mode nlp-analyze`）
  - `quant/ml/hermes/` — Hermes agent 集成（Nous Research hermes-agent runtime；`--mode hermes-chat`）
  - `quant/ml/gbm/` — LightGBM 因子聚合（可选依赖）
- 核心哲学：**策略要简单、验证要严格、参数要能在线微调、GitHub Actions 只做编排、实盘永远在本地**

---

## 1. 仓库结构（一屏认知）

```
quant/
├── main.py                     # 统一入口，用 --mode 选择功能
├── pyproject.toml              # uv 管理，lightgbm 为可选依赖 [ml]
├── README.md                   # 英文速览
├── AGENTS.md                   # ← 你现在读的这份
├── TODO                        # 开放性问题
├── .env.example                # 复制为 .env 后填写（Hermes / JQ / QMT 等凭据）
├── config/                     # TOML 配置（final_freeze_2025.toml 为冻结生产版）
├── docs/                       # 中文文档（见 docs/00_INDEX.md）
│   ├── 00_INDEX.md
│   ├── 01_foundation.md        # 演进 + 交易笔记 + 风控 + 量化利弊
│   ├── 02_strategy.md          # 截面策略 + 量价心理 + 形态例子 + 流派综述
│   ├── 03_engine.md            # 引擎参数 + 冻结手册 + 提交清单
│   ├── 04_joinquant.md         # 聚宽 3 篇合一
│   ├── 05_live.md              # 实盘/网关 6 篇合一
│   ├── 06_regulation.md        # 量化监管收紧应对
│   ├── 07_ai_research.md       # ML/AI 集成（含 Hermes/GBM MVP 手册）
│   └── 90_reference.md         # 参考资料
├── quant/                      # 主 Python 包
│   ├── app.py                  # 每个 --mode 对应的应用函数
│   ├── config.py               # AppConfig（Data / Strategy / Daemon / Risk / Gateway / Execution）
│   ├── backtest/               # 回测引擎
│   ├── stock_data/             # SQLite / JQData 客户端
│   ├── stock_filter/           # 价值预筛
│   ├── stock_exclusion/        # 可交易性过滤
│   ├── stock_strategy/         # 策略实现 + 打分聚合 + 生命周期 + EGD 在线学习
│   ├── execution/              # broker / paper / QMT HTTP / 拆单 / 风控
│   ├── live/                   # 盘前扫描 / 守护 / 分钟监控 / 日报 / 执行计划
│   ├── tools/                  # 优化 / 同步 / 因子分析 / walk-forward
│   ├── gateway/                # GatewayClient（Linux 调 Windows 网关）
│   ├── bridge/                 # 聚宽 → Redis 信号 schema
│   └── ml/                     # ML/AI 集成（都跑在主 quant venv, Py 3.12）
│       ├── llm/                # LLM 一次性调用（DeepSeek 直连 + JSONL→SQLite 幂等流水线）
│       ├── hermes/             # Hermes 桥（走 `hermes` CLI + profile；配对的 plugin 在 <repo>/hermes_plugin/）
│       └── gbm/                # LightGBM 因子聚合（可选依赖）
├── hermes_plugin/              # Hermes plugin（跑在 hermes venv, Py 3.11；symlink 到 ~/.hermes/profiles/quant/plugins/quant/）
│   ├── plugin.yaml
│   ├── __init__.py             # register(ctx)：注册 5 工具 + 4 skill
│   ├── tool_schemas.py
│   ├── skills/{analyst_zoe,info_charles,risk_kris,trader_ethan}/SKILL.md
│   ├── configs/deepseek_profile.example.yaml
│   ├── setup.sh                # 建 profile + symlink plugin + 写 profile .env
│   └── README.md
├── xtquant_gateway/            # Windows 侧网关服务
├── deploy/gateway_server/      # Windows 网关独立部署环境
├── scripts/                    # 运维脚本
├── test/                       # 聚宽端策略脚本
├── output/                     # 运行产物（.gitignore，白名单摘要 JSON 除外）
├── data/                       # 本地数据 & 模型（.gitignore；symlink 到大盘）
│   ├── runtime/*.db            # 主行情库
│   ├── models/                 # ML 模型（GBM 训练产物，动态生成）
│   └── nlp/                    # NLP 输入 JSONL + 输出 signals.db
└── .github/workflows/          # 每日回测漂移、因子分析、聚宽签到等 CI
```

---

## 2. "从零学量化 + 熟悉聚宽策略"最短路径

按顺序做，一天上手，一周读懂主流聚宽策略。

| 步 | 事情 | 位置 |
|----|------|------|
| 0 | 调整心态：接受 4 大局限 | `docs/01_foundation.md` §4 量化利弊 + §2/§3 |
| 1 | 掌握截面策略骨架 | `docs/02_strategy.md` §1；对照 `quant/stock_strategy/simple_3factor.py`、`jq_multifactor_clone.py` |
| 2 | 读懂"心理→量化"翻译 | `docs/02_strategy.md` §2、§3 |
| 3 | 熟悉引擎与参数 | `docs/03_engine.md` §1；跑一次 `--mode backtest` |
| 4 | 判断策略是否过拟合 | `--mode walk-forward`、`--mode factor-analyze` |
| 5 | 搬入聚宽策略 | `docs/04_joinquant.md` |
| 6 | 接实盘（先纸盘 1 周） | `docs/05_live.md` §1、§2、§4、§6 |

---

## 3. 常用命令速查

```bash
# 冻结版回测（最常用）
uv run python main.py --mode backtest --config config/final_freeze_2025.toml

# walk-forward 验证
uv run python main.py --mode walk-forward --config config/final_freeze_2025.toml

# 因子 IC/IR
uv run python main.py --mode factor-analyze --config config/final_freeze_2025.toml

# 快速优化管线
uv run python main.py --mode optimize-fast --config config/final_freeze_2025.toml

# 聚宽数据同步
uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml

# 纸盘全流程
uv run python main.py --mode live-auto --config config/live_paper.toml

# ← 新增：训练 GBM 因子聚合模型（需先 uv sync --extra ml）
uv run python main.py --mode gbm-train \
  --db-path data/runtime/market_csi1000_latest.db \
  --start-date 2023-01-01 --end-date 2025-06-30

# LLM 一次性结构化提取公告（DeepSeek 直连）
uv run python main.py --mode nlp-analyze --dry-run       # 冒烟
uv run python main.py --mode nlp-analyze                 # 实跑（需 DEEPSEEK_API_KEY）

# Hermes agent 多步对话（先 bash hermes_plugin/setup.sh 建 profile 并 symlink plugin）
uv run python main.py --mode hermes-chat --prompt "帮我看看 000001.XSHE 最近怎么样"
uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --skill info_charles --codes 000001.XSHE,600519.XSHG
uv run python main.py --mode hermes-chat --skill risk_kris --codes 000001.XSHE,600519.XSHG
uv run python main.py --mode hermes-chat --skill trader_ethan --code 000001.XSHE --date 2025-08-01
uv run python main.py --mode hermes-chat --profile alt --prompt "..."   # 换 profile 隔离
```

---

## 4. 关键设计决策（Cheatsheet）

摘自 `docs/01_foundation.md` §1，日常工作硬约束：

- **ADR-1**：GitHub Actions 只做编排（同步/漂移/告警），实盘交易永远本地。
- **ADR-2**：miniQMT 主链路 + QMT 全功能版兜底；`xtquant_gateway` API 一致。
- **ADR-3**：每个策略 ≤ 3 个核心因子；必须过 walk-forward；用在线学习慢速微调。
- **ADR-4**：`data/` 不进 git；`data/models/`、`data/nlp/` 全部动态生成。

---

## 5. 数据、配置、密钥（红线）

- **`data/`（含 `models/`、`nlp/`、`hermes_cache/`）、`output/*`（除白名单摘要）、`.env`、`uv.lock` 不进 git**。
- 聚宽账号密码、`QMT_GATEWAY_TOKEN`、`DEEPSEEK_API_KEY` 等**只放 `.env`**，或走 GitHub Secrets。绝不写进代码或 config。
- LLM 模型不入 git：仅通过 DeepSeek API（付费）调用；本仓库只存客户端代码 + prompt 模板。
- **hermes-agent runtime 独立安装、独立管理**：装在 `~/.hermes/`（含 Py 3.11 venv），本仓库不打包它。
  与 hermes 相关的凭据（DeepSeek key 等）放到 hermes profile 的 `.env`（例 `~/.hermes/profiles/quant/.env`），**不是**本仓库的 `.env`。
- **不要往 `quant/ml/hermes/` 塞跑在 hermes venv 里的代码**：那里只放主 venv 侧的桥。真正的 plugin 在**仓库根 `hermes_plugin/`**。
- LightGBM 是**可选依赖**（`pyproject.toml [project.optional-dependencies].ml`）；未装时 GBM 策略降级为中性分。

---

## 6. 修改仓库时的约定

1. **别写"注释解释代码在做什么"**。注释只用来解释非显然的权衡。
2. **策略参数改动**要么改 `config/*.toml`，要么改 `quant/config.py` 默认值。不 hardcode。
3. **新策略**必须在 `quant/stock_strategy/registry.py` 注册。
4. **回测/优化产物** 一律写到 `output/`，摘要类 JSON 白名单入 git。
5. **网关端点新增** 同步更新 `docs/05_live.md` §2、§3。
6. **ML 组件** 放到 `quant/ml/`，接口走 SQLite/JSON/HTTP，必须提供降级模式。
7. **文档更新**：一级主题一份 `docs/NN_topic.md`；新增文档取下一个可用编号；不改动已有编号（除非批量重排并同步 `AGENTS.md`）。
8. **文档与实现对齐**：功能真正落地 → 把该项从 `07_ai_research.md`（或其他计划性小节）移到主文档；代码删除时同步清理相关章节。
9. **提交前自查**：
   - 回测能跑通：`uv run python main.py --mode backtest --config config/final_freeze_2025.toml`
   - 没引入新 hardcode 路径或 token
   - 敏感文件没有 `git add`

---

## 7. AI 助手工作指南（面向未来的 agent）

- **理解目标**：让系统**稳定跑起来、可复现**，而不是不断堆新特性。
- **回测优先**：任何策略改动最好给出前后回测对比（`output/backtest_metrics.json`）。
- **默认只读**：需要写盘/联网/发单的操作，先给出计划，再确认再执行。**永远不要在没有明确指令时切到 `broker_type = "qmt_http"` 或去掉 `dry_run`。**
- **数据尊重**：不清理/覆盖 `data/`、`output/`、`.env`。
- **文档同源**：改代码同时改文档，尤其是 `config/*.toml` 字段变动 → `docs/03_engine.md` §1。
- **文档语言**：文档中文；代码注释可英文；错误消息面向用户时中文。
- **文档命名**：`docs/NN_topic.md`；不要用中文文件名；不要引入新子目录。
- **ML/AI 组件**：优先使用 `quant/ml/` 下已有基础设施（LLM 层 / Hermes 桥 / GBM 训练器）；模型/权重永远不入 git。
- **术语澄清**：本项目里 **Hermes 特指 [Nous Research hermes-agent](https://github.com/NousResearch/hermes-agent) 这个 agent runtime**，不是 LLM。真正的 LLM 是 DeepSeek。
- **两个 venv 不混**：`quant/ml/hermes/` 只放主 quant venv（Py 3.12）里跑的代码；`hermes_plugin/`（仓库根）只放跑在 hermes venv（Py 3.11）里的 plugin 代码。两边通过 subprocess 通信，别互相 import。
- **不夸大功能**：评估类文档必须标注"尚未实现"或"MVP 待验证"。

---

## 8. 常见问题速答

| 问题 | 快速回答 |
|------|---------|
| 我要在另一台机器上跑，怎么初始化数据？ | `git clone` → `uv sync` → `main.py --mode jq-bulk-sync` |
| 聚宽额度用完了怎么办？ | `.github/workflows/jq_signin.yml` 自动签到；额度用完等第二天。 |
| 想复现某个聚宽社区策略？ | 先看是不是"截面+排序+top-N"；是就改 `simple_3factor.py` / `jq_multifactor_clone.py`。 |
| 策略上线前最少要做什么？ | walk-forward 通过 + 纸盘 1 周 + 网关验收通过。 |
| miniQMT 淘汰了怎么办？ | ADR-2 已准备：切 QMT 全功能版，`xtquant_gateway` 只改连接方式。 |
| 参数调得太狠回测爆表？ | 大概率过拟合。跑 `--mode walk-forward` 判断。 |
| 想加海龟 / 缠论 / 深度学习？ | `docs/02_strategy.md` §4 + `docs/07_ai_research.md` 都有决策与门槛。 |
| 券商发风险提示、监管收紧？ | `docs/06_regulation.md` 里的收紧参数模板。 |
| **公告批量结构化怎么起？** | `.env` 填 `DEEPSEEK_API_KEY`；把 JSONL 放 `data/nlp/inbox/`；`main.py --mode nlp-analyze [--dry-run]`。 |
| **Hermes agent 怎么起？** | 已装 hermes → `bash hermes_plugin/setup.sh`（建 profile `quant`，symlink plugin，写 profile .env）→ `hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'` → `main.py --mode hermes-chat --skill analyst_zoe --code ...`。 |
| **四大 AI 角色是啥？** | `analyst_zoe`（基本面）、`info_charles`（舆情事件）、`risk_kris`（组合风控）、`trader_ethan`（短线价量）——都是 `hermes_plugin/skills/` 的 markdown。 |
| **能否用别的 profile？** | 能。`main.py --mode hermes-chat --profile <name>`；setup 时 `bash hermes_plugin/setup.sh <name>`。默认 `quant`。 |
| **GBM 怎么训？** | `uv sync --extra ml`（装 lightgbm）→ `main.py --mode gbm-train --db-path .../csi1000_latest.db`；产物 `data/models/gbm_multifactor.txt`。 |
| GBM 未训 / lightgbm 未装策略会挂吗？ | 不会，会降级为中性分（全 0），主链路继续跑。 |
| 没装 hermes / profile 没建 会怎样？ | `--mode hermes-chat` 返回 `{"error": ..., "hint": ...}` 提示下一步；其他 mode 完全不受影响。 |

---

## 9. 相关外部资源

- 聚宽 API：<https://www.joinquant.com/help/api/help>
- 聚宽 JQData：<https://www.joinquant.com/help/api/doc?name=JQDatadoc&id=9842>
- 迅投 XtQuant / miniQMT：<https://dict.thinktrader.net/>
- DeepSeek API：<https://platform.deepseek.com>
- Nous Research hermes-agent：<https://hermes-agent.nousresearch.com/docs>
- Hermes profiles：<https://hermes-agent.nousresearch.com/docs/user-guide/profiles>
- agentskills.io 规范：<https://agentskills.io/specification.md>
- LightGBM：<https://lightgbm.readthedocs.io>
- 本项目 GitHub 远端：`git@github.com:com0do/quant.git`

---

> 觉得某处过时或有误，请一并更新这份 `AGENTS.md` 和对应的 `docs/NN_*.md`。
> **代码是术，文档是道，风控是命。**
