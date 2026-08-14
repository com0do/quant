# 机器学习与 AI 集成研究计划

> **现状（本次更新）**：仓库内 ML/AI 分层如下。
>
> **已落地（MVP，待验证）**：
>
> - **LLM 一次性调用层**：`quant/ml/llm/`。直连 DeepSeek 官方 API（OpenAI 兼容协议）。
>   批量公告 JSONL → SQLite 结构化信号流水线。**无状态、无工具、无记忆**。
>   入口 `main.py --mode nlp-analyze [--dry-run]`。
>
> - **Hermes agent 集成**：分两处
>   - `quant/ml/hermes/` — 主 quant venv（Py 3.12）里的桥：runner 走 `hermes -z` CLI + profile；rpc 是被 plugin 反向调回的端点
>   - `hermes_plugin/` — Hermes plugin 本体（跑在 hermes 的 Py 3.11 venv 里）：`plugin.yaml + __init__.py + tool_schemas + skills/`
>
>   桥接 **Nous Research 的 hermes-agent runtime**（不是 LLM）。四大 AI 角色 = 四个 agentskills.io 标准 skill：
>   `analyst_zoe / info_charles / risk_kris / trader_ethan`。5 个 quant 工具（`get_kbars / get_fundamentals /
>   get_announcements / compute_factor / run_backtest_slice`）通过 plugin 注册到 hermes。模型后端用 DeepSeek，
>   通过 hermes profile config 挂载。用 `hermes profile` 机制隔离，不动用户已有 profile。
>   入口 `main.py --mode hermes-chat [--profile <name>] [--prompt "..." | --skill <name>]`。
>
> - **GBM 因子聚合**：`quant/ml/gbm/` + `quant/stock_strategy/gbm_multifactor.py`。
>   LightGBM 为可选依赖，未装或模型缺失时策略降级为中性分。入口 `main.py --mode gbm-train`。
>
> **未落地**：异常检测、独立 RAG、执行层 RL、财报 PDF 自动抓取。
>
> **唯一预先存在**的 ML 组件仍是 `quant/stock_strategy/online_learning.py`（EGD）。
>
> 任何写在本文档"评估/计划"里的内容如果落地了，请把该项从这里移动到对应策略/执行文档里。

---

## 一、我们要用 ML/AI 解决什么问题

回到 ADR-3 的原则："**策略要简单，验证要严格，参数要能在线微调**"。

ML/AI 的位置只能是**辅助**——不允许它取代策略主逻辑或风控。

按"低风险/高价值"排序，可能的切入点有 4 个：

| # | 场景 | 价值 | 风险 | 推荐优先级 |
|---|------|------|------|-----------|
| 1 | 财报/公告的 NLP 结构化 | 高 | 低 | ⭐⭐⭐（先做） |
| 2 | 因子挖掘（GBM 排序） | 中 | 中 | ⭐⭐（次做） |
| 3 | 异常检测（IsolationForest / Autoencoder） | 中 | 低 | ⭐⭐（次做） |
| 4 | 强化学习（RL）用于执行拆单 | 中 | 高 | ⭐（远期） |

**不做**的事情：

- **不用** RL 做买卖决策（样本效率、非平稳、可解释性都不合格）。
- **不用** CNN 直接读 K 线图（过拟合风险巨大且不可解释）。
- **不用** LLM 直接输出买卖信号（幻觉、prompt 依赖强、无法回测）。

## 二、逐项评估

### 2.1 强化学习（RL）

- **常见幻想**：用 PPO/A3C/DQN 学"选股策略"或"仓位管理"。
- **真实约束**：
  - A 股一天一根 K 线，训练一次需要几十年样本；样本效率远不够。
  - 市场非平稳（regime shift），RL 训练出来的策略在下一个 regime 里就失效。
  - 奖励函数难设计——用净值涨幅？回撤惩罚？夏普？每种都会引导出奇怪的行为。
  - 无法解释：模型出错时找不到根因。
- **仅有意义的场景**：**订单执行拆单**（TWAP/VWAP 参数选择）——有清晰即时奖励（成交均价 vs VWAP 基准），可以在几万笔样本内收敛。
- **结论**：**决策层不做 RL；执行层可以作为远期研究项目**（Phase 3 之后）。

### 2.2 AI Agent（Hermes agent + DeepSeek 作为模型后端）

**术语澄清**：本项目里 **Hermes 特指 [Nous Research hermes-agent](https://github.com/NousResearch/hermes-agent)（一个 agent runtime）**，
**不是** Hermes-3 那种微调后的 LLM。真正的 LLM 是 DeepSeek，通过 hermes 的 config 挂上来。

- **两种合理定位**：
  1. **LLM 一次性调用**（无 agent）—— 读非结构化文本，出结构化 JSON。批量公告结构化就属于这里。走 `quant/ml/llm/`，直连 DeepSeek。
  2. **Agent 多步推理**（Hermes runtime）—— 会用工具的助手：先查行情/公告，再综合，再产出观点。走 `quant/ml/hermes/`。
- **典型任务**：
  - 一次性：财报要点抽取、公告情感分类、风险标签打标 → LLM 层就够
  - 多步：给一只股票+日期，让 agent 组织一份"基本面 + 事件 + 价量"综合简报 → Hermes agent 层
- **落地路径（已实现基础设施）**：
  1. LLM 层：JSONL 公告输入 → DeepSeek 结构化 → `data/nlp/signals.db.nlp_signals`（`--mode nlp-analyze`）
  2. Hermes plugin 注册 5 个工具（kbars / fundamentals / announcements / factors / backtest_slice）
  3. 四大 skill（`analyst_zoe / info_charles / risk_kris / trader_ethan`）以 markdown 方式挂到 plugin 下
  4. `--mode hermes-chat` 从主 venv 通过 subprocess 桥调 hermes-agent（因它强制 Py 3.11，主工程是 Py 3.12）
- **风险控制**：
  - 单轮 LLM 输出必须严格 JSON；解析失败写 neutral + raw_error（LLM 层已实现）
  - Agent 场景禁买卖建议、禁编造数据、禁越权调工具（skill 里已写硬性约束）
  - 每份文档必须能溯源（保留 `source_text_hash` 主键）
  - 定期抽样人工复核（≥ 10 份/周）
- **依赖成本**：DeepSeek 每千 token 成本 ¥0.001-0.004；一份千字公告约 ¥0.005。Hermes agent 一次多步任务估算 ¥0.02-0.1。
- **结论**：**这是最有价值也最应该先做的 AI 方向**。走 Phase 3。

### 2.3 RAG（检索增强生成）

- **单独没有价值**，只在 Agent 场景中作为底层组件。
- **典型用途**：给 hermes-agent 提供公司过去 3 年财报/公告作为上下文，让它做"环比对比"、"识别管理层言论转向"。
- **落地方式**：`data/documents_index/`（chroma 或 faiss 向量库）+ 简单检索包装。
- **不做**的事：不做"用 RAG 直接回答'今天该不该买 XX 股'"这种糊涂问题；RAG 的输出仍然要通过因子/信号的严格验证。
- **结论**：**跟随 Agent 落地**，不作为独立项目。

### 2.4 CNN + GBM 量化选股

社区常见提法是"用 CNN 提取 K 线/时间序列特征，用 GBM (LightGBM/XGBoost) 做最终排序"。

- **CNN 部分**：
  - **风险高**：可解释性差、过拟合空间大、A 股 K 线样本量对 CNN 来说仍然偏少。
  - **本项目取舍**：**不做 CNN**（与 ADR-3 冲突严重）。
- **GBM 部分**：
  - 相对可控——只是"更好的多因子加权"。GBM 能捕捉因子间非线性，比 z-score 加权更强。
  - **可以做**：先做一个 `simple_3factor_gbm` 的 baseline，输入还是那几个因子，输出是 GBM 打分，与线性加权版做 head-to-head 对比。
  - **门槛**：GBM 版必须通过与线性版**至少同样严格**的 walk-forward，且样本外夏普提升 ≥ 20% 才值得替换。
- **结论**：
  - **CNN 不做**。
  - **GBM 作为线性加权的候选替代做研究实验**，但优先级低于 NLP Agent。

### 2.5 异常检测

- **场景**：识别市场异常日（闪崩、异常放量、成分股集体尾盘拉抬），作为风控的额外输入。
- **方法**：Isolation Forest / LSTM-Autoencoder / One-Class SVM。
- **不作为**：买卖信号；只用于"暂停交易"的触发。
- **落地路径**：
  1. 用历史数据训练一个 IsolationForest（sklearn 一个 import 就够）
  2. 每日盘前跑一次，若"异常概率 > 阈值"，daemon 直接空仓一天
  3. 与现有 `crash_ret5_threshold` 等硬规则并列存在
- **结论**：**中等优先级**，与 NLP Agent 并列作为 Phase 3 的一部分。

## 三、依赖引入决策

引入任何一个 ML/AI 组件前，先问自己：

1. 是否可以在**独立 venv/subprocess** 里运行？（避免污染主 quant venv）
2. 是否有**离线可用**的替代方案？（避免依赖商业 API 中断）
3. 是否可以**产出结构化中间结果**（JSON/parquet），让主链路可以"AI 缺席"时降级到无 AI 版本？

如果 3 个答案都是"是"，才允许在 `pyproject.toml` 加依赖。

推荐的引入顺序（严格逐个来，不并行）：

1. `scikit-learn`（用于 IsolationForest / LightGBM 的 baseline）
2. `lightgbm`（GBM 排序候选）
3. `chromadb` 或 `faiss-cpu`（RAG 向量库）
4. Agent 客户端（先本地 llama.cpp / Ollama，再考虑云端 API）

**明确不引入**：`torch`、`tensorflow`、`keras`。任何需要它们的组件都属于 "Phase 3 之后"，需要单独立项。

## 三-补 A. LLM 一次性调用层（`quant/ml/llm/`）使用手册

**定位**：文本进 → 结构化 JSON 出。**无状态、无工具、无记忆**。批量公告分析的正解。

**位置**：`quant/ml/llm/deepseek.py` / `schema.py` / `announcement_pipeline.py` / `prompts/announcement_zh.md` / `README.md`。

**依赖策略**：只依赖仓库已有的 `requests`；不需要装任何额外包。

**三步上手**：

1. `.env` 里配 `DEEPSEEK_API_KEY`（可复制根目录 `.env.example`）。
2. 把公告 JSONL 放 `data/nlp/inbox/*.jsonl`，一行一份，字段 `code / date / title / text`。
3. 跑：
   - 冒烟：`uv run python main.py --mode nlp-analyze --dry-run`
   - 实跑：`uv run python main.py --mode nlp-analyze`
   - 限量：`--limit 20`

**产物**：`data/nlp/signals.db` 的 `nlp_signals` 表；`source_text_hash` 做幂等。

**降级模式**：

- 缺 `DEEPSEEK_API_KEY` 或 `/models` 探活失败 → 整个批次直接返回、不写库。
- 单条 JSON 解析失败 → 写 neutral 记录，`raw_error` 保留错误，主流程不中断。

**下一步验证**：把 `sentiment_score * confidence` 作为新因子加入 `factor_analyze`，若 IC/IR 显著再进 walk-forward。

## 三-补 B. Hermes agent 集成（`quant/ml/hermes/` + `hermes_plugin/`）使用手册

**定位**：多步推理、要用工具、要记忆的场景。**不是** LLM 客户端。

**分层**（两个 venv 严格隔离，不互相 import）：

- **主 quant venv（Py 3.12）—— `quant/ml/hermes/`**
  - `runner.py` — 主 venv 侧入口，`chat()` / `run_skill()`；实现是 `subprocess.run(["hermes","-z",...], env={..., "HERMES_HOME": profile_dir})`
  - `rpc.py` — 主 venv 侧 RPC 端点；plugin 通过 `uv run python -m quant.ml.hermes.rpc <cmd> <args>` 反向调
  - `__init__.py` / `README.md`

- **Hermes venv（Py 3.11）—— 仓库根 `hermes_plugin/`**
  - `plugin.yaml` — manifest
  - `__init__.py` — `register(ctx)`：注册 5 工具 + 4 skill（只用 stdlib）
  - `tool_schemas.py` — 5 个工具的 JSON schema
  - `skills/{analyst_zoe,info_charles,risk_kris,trader_ethan}/SKILL.md`
  - `configs/deepseek_profile.example.yaml` — profile config.yaml 参考片段
  - `setup.sh` — profile 模式一键安装
  - `README.md`

**为什么两处**：hermes-agent 强制 Python 3.11，主 quant 工程是 Python 3.12。**两个 venv 的代码不能塞同一目录树**，不然容易误互 import 或误装。plugin 逻辑放仓库根，主 venv 桥放 `quant/ml/`。

**为什么用 profile**：hermes 支持多 profile（`~/.hermes/profiles/<name>/`），每个 profile 独立 `config.yaml / .env / skills / plugins / memories`。用一个专门的 `quant` profile 装本插件，**不打扰你已有的默认 profile**。切换靠 `HERMES_HOME` 环境变量（setup.sh 也顺便建了 `~/.local/bin/quant` wrapper alias）。

**四步上手**：

1. 前提：`hermes` CLI 已在 PATH（`which hermes` 有输出）；DeepSeek 已在默认 profile 的 `.env` 配好
2. `bash hermes_plugin/setup.sh` — 建 profile `quant`（`hermes profile quant --clone` 继承 DeepSeek key），symlink plugin，写 `QUANT_REPO_PATH` 到 profile `.env`
3. 启用 quant toolset：
   ```bash
   hermes config set --profile quant agent.enabled_toolsets '["core","quant"]'
   ```
   或手动编辑 `~/.hermes/profiles/quant/config.yaml`，参考 `hermes_plugin/configs/deepseek_profile.example.yaml`
4. 跑：
   - 自由对话：`uv run python main.py --mode hermes-chat --prompt "..."`
   - 分析师 Zoe：`uv run python main.py --mode hermes-chat --skill analyst_zoe --code 000001.XSHE --date 2025-08-01`
   - 情报官 Charles：`--skill info_charles --codes 000001.XSHE,600519.XSHG --date 2025-08-01`
   - 风控官 Kris：`--skill risk_kris --codes ...`
   - 交易员 Ethan：`--skill trader_ethan --code ... --date ...`
   - 换 profile：`--profile my_other_profile ...`

**产物**：`output/hermes_chat_last.json`（最近一次会话结果快照）。

**降级模式**：
- `hermes` CLI 找不到 → `runner.chat()` 返回 `{"error": "hermes CLI not found on PATH", "hint": "..."}`
- profile 目录不存在 / plugin symlink 缺失 → 返回 `{"error": "hermes profile 'xxx' not set up", "hint": "bash hermes_plugin/setup.sh"}`
- RPC 超时 → 返回结构化 error，agent 会转达给用户
- DB 缺失 → 工具返回 `error + hint`，skill 已教 agent 转达
- 以上任何情况都**不影响主 quant 链路**（backtest / walk-forward / live-auto 等）

**四大角色分工**：

| 角色 | 职责 | 触发词 | 主要工具 |
|---|---|---|---|
| 分析师 Zoe | 基本面深度解读 | 分析/财报/公告 | fundamentals + announcements + kbars |
| 情报官 Charles | 舆情/事件排序 | 情报/舆情/异动 | announcements + kbars |
| 风控官 Kris | 组合风险审查 | 风控/回撤/压力测试 | kbars + announcements + (backtest) |
| 交易员 Ethan | 短线价量结构 | 短线/关键位/支撑压力 | kbars |

## 三-补 C. GBM 因子聚合 MVP 使用手册

**位置**：`quant/ml/gbm/factor_gbm.py`（训练 + 推理）、`quant/stock_strategy/gbm_multifactor.py`（策略）。

**依赖策略**：`lightgbm` 声明为**可选依赖**（`pyproject.toml` 的 `[project.optional-dependencies].ml`）。未安装时策略降级为中性分（全 0），主链路不受影响。

**装依赖**：`uv sync --extra ml`

**特征列（与 `simple_3factor` 对齐，方便 head-to-head）**：`pe_ratio / pb_ratio / roe / ret_20 / ret_60 / vol_20`

**标签**：前向 5 日收益率的**截面去均值**，避免学到"大盘涨跌"这个共同信号。

**训练**：

```bash
# 用 config 里 data.runtime_csi1000_db_path 指向的库训练
uv run python main.py --mode gbm-train --config config/final_freeze_2025.toml

# 或明确指定
uv run python main.py --mode gbm-train \
  --db-path data/runtime/market_csi1000_latest.db \
  --start-date 2023-01-01 --end-date 2025-06-30 \
  --forward-days 5 \
  --model-path data/models/gbm_multifactor.txt
```

**产物**：

- `data/models/gbm_multifactor.txt`：LightGBM 原生格式（不入 git，`.gitignore` 已排除 `data/models/`）。
- `data/models/gbm_multifactor.metrics.json`：训练/验证 metrics（含 rank IC、IR、feature importance）。
- `output/gbm_train_summary.json`：本次训练摘要（入 git 白名单）。

**推理**：在 `config/*.toml` 里把 `strategy_names` 加上 `"gbm_multifactor"`，或直接用它替换某个多因子策略跑对比。

**验证门槛**（同 `docs/02_strategy.md` 第 4 节的通用门槛）：

1. `main.py --mode walk-forward` 三段窗口至少两段正超额；
2. 样本外 rank IC 均值 > 0.03 且 IR > 0.5；
3. 与 `csi1000_enhanced` 做一个季度 head-to-head，样本外夏普提升 ≥ 20% 才值得替换。

**当前状态**：MVP 代码可跑，无实测样本外表现。不建议直接切生产。

## 四、落地路线图（对齐 `docs/01_foundation.md` 第 1 节演进计划的 Phase）

### Phase 3.1（4-6 周）：异常检测（低风险起步）

- 加 `sklearn` 依赖
- 新建 `quant/tools/anomaly_detect.py`
- 用 `market_features`（广度、波动、成交额）训练 IsolationForest
- 每日盘前跑，输出到 `data/runtime/anomaly_score.json`
- `quant/live/daemon.py` 读取该文件，若异常分 > 阈值则强制空仓
- 验证：至少覆盖 2020 疫情 / 2024 春节小盘崩溃两个历史事件

### Phase 3.2（6-8 周）：NLP + Hermes agent   **←  MVP 已落地**

- **LLM 层（一次性结构化提取）已落地**：DeepSeek 直连客户端 + JSONL→SQLite 流水线 + 幂等 + 降级（`quant/ml/llm/`）
- **Hermes agent 层已落地**：runner + RPC + plugin + 5 工具 + 4 skill（`quant/ml/hermes/`）
- **待办**：
  - 用户手动 `bash quant/ml/hermes/setup.sh` 装 hermes-agent + 配 `~/.hermes/config.yaml`
  - 增加"聚宽公告拉取器"，把 JQ 定时更新的 `announcement` 结构化数据转成 JSONL
  - 在 `factor_analyze` 里加载 `nlp_signals` 表，做 IC/IR
  - 通过后把显著字段（例如 `sentiment_score * confidence`、`risk_flags` one-hot）加入 `simple_3factor` / `gbm_multifactor` 的特征
  - 4 个 skill 首次实测：用近 30 日已入库公告的股票，四种 skill 各跑一次，人工评审输出质量

### Phase 3.3（6-8 周）：GBM 因子聚合   **←  MVP 已落地**

- **已落地**：`quant/ml/gbm/factor_gbm.py` + `quant/stock_strategy/gbm_multifactor.py`；lightgbm 为可选依赖
- **待办**：
  - 在 config 里加 GBM 训练超参段（`num_leaves`、`learning_rate`、`forward_days`）
  - 与 `simple_3factor` 做 walk-forward head-to-head
  - 若通过，考虑把 NLP 情感因子作为额外列一起训练（跨阶段依赖）

### Phase 3.4（12+ 周）：RAG + Agent 深度分析

- 建 chroma 向量库
- Agent 支持"跨期对比"、"同行业对比"
- 输出结构化增量信号

### 远期：执行层 RL（Phase 4）

- 只用于订单拆单参数选择
- 单独 subprocess，训练与推理分离
- 与主策略解耦

## 五、失败判定与退出

任一项目**满足以下任何一条**，立即暂停并进入复盘：

- 引入依赖后，主 quant venv `uv sync` 失败或 CI 变红
- 落地组件的产出让 `main.py --mode backtest` 结果不可复现（同一 seed 不同结果）
- 组件运行时占用超过 4GB 内存或 10 分钟延迟主链路
- 3 个月内不能提供正向 walk-forward 结果

## 六、代码组织约定（已实施）

- ML/AI 主 venv 侧组件放到 `quant/ml/` 子包，与主策略隔离；当前包含：
  - `llm/` — 一次性 LLM 调用（无 agent，无工具，无记忆）
  - `hermes/` — Hermes 桥（走 `hermes` CLI + profile，rpc 端点）
  - `gbm/` — LightGBM 因子聚合
- **跨 venv 的 hermes plugin 单独放仓库根 `hermes_plugin/`**（跑在 hermes 的 Py 3.11 venv 里）。两处代码互不 import。
- 与主链路的接口只走 **SQLite / JSON 文件 + subprocess**，不做进程内直接引用（避免主链路 import 时被拖累）。
- 每个组件都提供了"降级模式"：
  - LLM 层：DEEPSEEK_API_KEY 缺失 / 端点不可达 → 整批返回；单条解析失败 → neutral + `raw_error`
  - Hermes 桥：hermes 未装 / profile 未建 / plugin 未 symlink → 返回 `{"error": ..., "hint": ...}`
  - GBM 模型缺失 / lightgbm 未装 → 全 0 分，主链路继续跑
- 二进制资产（模型权重、缓存、向量库）都在 `.gitignore` 里（`data/models/`、`data/hermes_cache/`、`data/nlp/`）。
- **hermes-agent 本身不作为 pip 依赖**（它强制 Py 3.11，我们主工程 Py 3.12），通过 `bash hermes_plugin/setup.sh` 挂到用户已有的 `~/.hermes/` profile 目录下。

## 七、与其他文档的关系

- 与 `docs/10_foundation/01_evolution_plan.md` 的 Phase 3 保持一致，本文是它的细化。
- 与 `docs/10_foundation/04_quant_pros_cons.md` 的 4 个反思问题保持一致：任何 ML 组件都要过那 4 关。
- 与 `docs/60_regulation/01_quant_regulation_playbook.md` 一致：ML 不能增加交易频次，只能提高单笔命中率。

---

## 八、更新历史

- 初版：明确 RL/Hermes/RAG/CNN+GBM 四条路的取舍。当前无 ML 依赖，只有 EGD 在线学习。
- 2026-08：LLM 层与 Hermes agent 层分离；澄清 Hermes 指的是 Nous Research hermes-agent runtime（不是 LLM）；DeepSeek 作为统一模型后端；四大 skill 骨架落地。
- 2026-08（refactor）：Hermes plugin 从 `quant/ml/hermes/plugin/` 挪到仓库根 `hermes_plugin/`（彻底隔离 Py 3.11 / 3.12 两个 venv 的代码）；runner 改走 `hermes -z` CLI（弃用 `_agent_shim.py`），走 `HERMES_HOME` + profile 隔离，不动用户已有的默认 profile。
