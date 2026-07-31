# Quant 系统演进计划

> 基于当前架构的深度审查 + 外部依赖调研，识别核心矛盾，给出分阶段演进路线。

---

## 一、当前架构总结

```
┌── 数据层 ────────────────────────────────────────────┐
│  JoinQuant (聚宽) ──jqdatasdk──▶ SQLite (按指数分库)   │
│  scripts/jq_bulk_sync_smallcap.py                    │
│  scripts/run_tomorrow_sync.sh                        │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌── 分析层 ────────────────────────────────────────────┐
│  quant/backtest/engine.py    回测引擎                 │
│  quant/stock_strategy/       7 个策略（生产用1个）      │
│  quant/tools/optimize.py     网格搜索 + 前向验证       │
│  quant/tools/vectorbt_optimize.py  快速动量预筛选      │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌── 执行层 ────────────────────────────────────────────┐
│  main.py --mode live-auto                            │
│  quant/live/daemon.py       实盘守护进程              │
│  quant/execution/qmt_http_broker.py  ──HTTP──▶       │
│                                                     │
│  ┌──── Windows 11 ──────────────────────────┐       │
│  │  xtquant_gateway/ (FastAPI)              │       │
│  │       │                                   │       │
│  │  MiniQMT (xtquant.xttrader)              │       │
│  └──────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────┘
```

**现状评估**：架构完整、模块化好、文档齐全。核心风险在外部依赖（MiniQMT 淘汰、JQ 配额）和策略演进能力。

---

## 二、核心矛盾与权衡

### 矛盾 1：GitHub Actions 周期驱动 vs Windows QMT 执行

| 维度 | 分析 |
|------|------|
| **矛盾本质** | TODO 期望用 GitHub Actions 周期驱动交易，但 QMT/MiniQMT 是 Windows GUI 程序，不适合 CI 环境 |
| **为什么不能在 GitHub Actions 跑 QMT** | 1) 券商账号密码不能放 CI；2) QMT 登录需要 GUI 交互；3) 网络环境（券商 VPN/专线）不可控；4) 安全合规风险 |
| **权衡结论** | **GitHub Actions ≠ 执行环境。它是编排层。** |

**推荐分工**：

```
GitHub Actions (云端)                 本地机器 (Windows/Linux)
┌──────────────────────┐            ┌──────────────────────────┐
│ 定时触发 (schedule)   │            │                          │
│ JQ 数据同步 (盘后)     │            │ 盘中实时行情 (QMT)        │
│ 回测验证 (参数漂移检测) │            │ 实盘交易执行              │
│ 因子分析 + 信号生成    │            │ 风控熔断                  │
│ 策略参数自动优化       │            │ 日终报告                  │
│ 异常告警              │            │                          │
└──────┬───────────────┘            └──────────────────────────┘
       │                                      ▲
       │  git push 信号/参数                   │
       └──────────────────────────────────────┘
              git pull (本地拉取)
```

### 矛盾 2：MiniQMT 长期可行性

| 维度 | 分析 |
|------|------|
| **现状** | 国金证券目前仍支持 MiniQMT，门槛低 |
| **风险** | 至少一家券商已公告 2026 年停止 MiniQMT 新申请，存量用户后续也将逐步停止 |
| **好消息** | XtQuant API 在 MiniQMT 和 QMT 之间是**兼容的**——QMT 全功能版同样提供 `xtdata` + `xttrader` |
| **权衡结论** | **保持当前 MiniQMT 网关，但要准备好 QMT 降级路径。** |

**迁移路径（如果需要）**：

```
当前: Linux ──HTTP──▶ MiniQMT (Windows)
未来: Linux ──HTTP──▶ QMT API Server (Windows)
                      │
                      └── QMT 全功能版同样支持 xtquant Python API
                          只需调整 xtquant_gateway 的连接方式
```

**建议**：在 `xtquant_gateway` 中增加对 QMT 全功能版（非极简模式）的连接支持，作为 fallback。两种模式的 `xtquant` API 是相同的，差异仅在登录/初始化环节。

### 矛盾 3：策略简单 vs 策略复杂（过拟合）

| 维度 | 分析 |
|------|------|
| **矛盾本质** | 历史数据过度优化 → 过拟合 → 实盘失效。但简单策略又不够好。 |
| **当前做法** | 7 个策略，但生产只用 1 个（`csi1000_enhanced`）；使用 regime switch 做自适应 |
| **问题** | regime switch 是静态规则（`breadth > 0.60 → risk_on`），不随市场结构变化而演进 |

**权衡结论：策略要简单，但验证要严格，自适应要在线。**

```
简单策略 + 严格验证 + 在线微调
   ▲            ▲            ▲
   │            │            └── 在线学习（参数缓慢漂移）
   │            └── walk-forward 验证（不是静态回测）
   └── 策略逻辑 ≤3 个核心因子
```

| 原则 | 具体做法 |
|------|---------|
| **策略简单** | 每个策略 ≤3 个核心因子，逻辑可解释 |
| **验证严格** | 必须通过 walk-forward（滚动窗口）验证，不能只看全样本回测 |
| **在线微调** | 使用在线学习（如 exponentiated gradient）让参数随市场缓慢漂移 |
| **集成而非堆叠** | 多个简单策略的集成（ensemble）优于一个复杂策略 |
| **定期退役** | 每个策略有生命周期，定期评估是否失效，失效就退役 |

### 矛盾 4：数据本地化 vs 多机器同步

| 维度 | 分析 |
|------|------|
| **矛盾本质** | SQLite 数据库（几百 MB～几 GB）不适合 git，但需要在多台电脑工作 |
| **权衡结论** | **代码同步用 git，数据同步用脚本。** |

```
git 管理（轻量）                    脚本管理（重量）
┌─────────────────┐              ┌─────────────────────┐
│ *.py             │              │ data/runtime/*.db    │
│ *.toml           │              │ data/archive/*.db    │
│ *.sh             │              │ data/meta/*.db       │
│ docs/*.md        │              │ output/*.csv         │
│ .gitignore       │              │ *.lock               │
└─────────────────┘              └─────────────────────┘
```

**每台机器的数据初始化**：运行一次 `scripts/jq_bulk_sync_smallcap.py` 从 JQ 拉取全量数据，之后每天增量同步。

### 矛盾 5：ML/AI Agent 何时引入

| 维度 | 分析 |
|------|------|
| **TODO 诉求** | "使用机器学习、AI agent (hermes) 做数据分析、财报分析、数据闭环" |
| **当前状态** | 无 ML，回测引擎用纯手工技术特征（MA、MACD、K线形态等） |
| **错误路径** | 直接用 ML 模型替代现有策略 → 黑箱化 → 不可解释 → 风险不可控 |
| **正确路径** | ML 做辅助分析（特征发现、异常检测），策略逻辑保持透明 |

**ML/AI 的合理切入点**（按优先级）：

1. **异常检测**（低风险、高价值）：用 ML 检测市场异常（如闪崩预警、异常放量），作为风控信号
2. **因子发现**（中风险）：用 ML 从财报/量价中挖掘新因子，经过 walk-forward 验证后加入策略池
3. **参数自适应**（中风险）：用在线学习微调策略参数（而非替代策略逻辑）
4. **NLP 财报分析**（低风险）：用 AI agent 读财报文本，提取结构化信号（如管理层语气、风险提示）

---

## 三、演进路线图

### Phase 0：加固当前架构（优先级最高，1-2 周）

**目标**：让现有系统在 MiniQMT 仍可用期间稳定跑起来，积累实盘经验。

| # | 任务 | 说明 |
|---|------|------|
| 0.1 | **完成 MiniQMT 网关联调** | 确保 `live_qmt_http.toml` + `xtquant_gateway/` + Windows MiniQMT 端到端跑通 |
| 0.2 | **建立每日运行节奏** | `run_daily_live_pipeline.sh` 在本地稳定运行（盘前同步 → 回测 → 扫描 → 实盘） |
| 0.3 | **纸盘先行** | `dry_run = true` 先跑 1-2 周纸盘，验证信号质量 |
| 0.4 | **补全 .gitignore** | 确保 `data/runtime/*.db`、`data/archive/*.db`、`output/`、`.env` 不进入 git |
| 0.5 | **跨机器同步验证** | 在第二台电脑上 `git clone` + `jq_bulk_sync`，确认能跑通完整流程 |

### Phase 1：引入 GitHub Actions（2-4 周）

**目标**：让 GitHub Actions 承担盘后数据同步 + 回测验证，本地只做执行。

| # | 任务 | 说明 |
|---|------|------|
| 1.1 | **创建 `.github/workflows/daily_sync.yml`** | 每个交易日 15:30（收盘后）触发：JQ 数据增量同步 → 推送更新后的 data 到仓库 |
| 1.2 | **创建 `.github/workflows/backtest_check.yml`** | 每次 push 或每日触发：在最新数据上跑回测，对比基准指标，漂移超阈值则告警 |
| 1.3 | **创建 `.github/workflows/factor_analysis.yml`** | 每周跑一次因子 IC/IR 分析，生成报告，检测因子衰减 |
| 1.4 | **策略参数存储为 Artifact** | 优化后的参数以 JSON/Toml 形式存储在 repo 中，不依赖本地文件 |
| 1.5 | **解决 JQ 凭据问题** | JQ 账号密码通过 GitHub Secrets 传入 CI；评估 JQ API 是否允许多 IP 登录 |

> **注意**：GitHub Actions 免费额度有限（~2000 min/月），数据同步脚本需控制运行时间。如果 JQ 禁止多 IP 同时登录，需要改为本地同步 + Actions 只做分析。

### Phase 2：策略演进能力（4-6 周）

**目标**：让策略具备"随市场自动微调"的能力，减少过拟合风险。

| # | 任务 | 说明 |
|---|------|------|
| 2.1 | **实现 walk-forward 回测框架** | 在现有回测引擎上增加滚动窗口训练/验证分离（如 12 个月训练 → 3 个月验证，滚动前进） |
| 2.2 | **简化策略到核心因子** | 将 `csi1000_enhanced` 拆解，保留 ≤3 个核心因子 + 明确的经济学解释 |
| 2.3 | **引入在线参数更新** | 使用 exponentiated gradient descent（EGD）在线更新策略权重，每天微调 ≤1% |
| 2.4 | **策略生命周期管理** | 每个策略记录"生效时间"和"退役条件"（如连续 3 个月夏普 < 0.5 则退役） |
| 2.5 | **集成简单策略 ensemble** | 2-3 个互补的简单策略做等权/动态加权 ensemble |

### Phase 3：ML/AI 引入（6-12 周）

**目标**：在策略稳定的基础上，逐步引入 ML 辅助信号。

| # | 任务 | 说明 |
|---|------|------|
| 3.1 | **异常检测模块** | 用 isolation forest / LSTM-autoencoder 检测市场异常日，作为风控输入（暂停交易） |
| 3.2 | **NLP 财报分析 Agent** | 用 hermes-agent 批量读取财报 PDF/公告，提取关键信号（盈利超预期、风险提示、行业趋势） |
| 3.3 | **因子挖掘 pipeline** | 从财报 N 维度 + 量价特征中自动发现新因子，走 walk-forward 验证后再加入策略池 |
| 3.4 | **数据闭环** | 实盘交易记录 → 特征存储 → 定期重新训练 → 参数更新 → 部署（完整 MLOps 循环） |

### Phase 4：多资产扩展（远期）

| # | 任务 | 说明 |
|---|------|------|
| 4.1 | **基金筛选模块** | ETF/LOF 筛选逻辑（费率、跟踪误差、流动性），复用现有回测引擎 |
| 4.2 | **债券/可转债** | 如果 QMT 支持债券交易，在 QMT 端配置；策略参数独立管理 |
| 4.3 | **资产配置层** | 在策略引擎上层增加股/债/基的大类配置模型（如风险平价） |

---

## 四、架构决策记录

### ADR-1：GitHub Actions 只做编排，不做执行

- **决策**：实盘交易永远在本地机器执行，GitHub Actions 承担数据同步、回测验证、告警
- **理由**：安全（不暴露券商凭据）、合规、QMT 无法在 CI 环境运行
- **替代方案**：全部本地（简单但缺少自动化验证）；云服务器（成本高，QMT 仍需 Windows）

### ADR-2：保持 MiniQMT 网关，准备 QMT fallback

- **决策**：继续使用 MiniQMT + XtQuant 网关方案，同时在 `xtquant_gateway` 中增加 QMT 全功能版兼容
- **理由**：MiniQMT 是当前最优方案（API 灵活、Python 原生），XQuant API 在两种模式下兼容
- **风险缓解**：如果 MiniQMT 被淘汰，迁到 QMT + API Server 模式（同 API，仅连接方式不同）

### ADR-3：策略要简单，验证要严格

- **决策**：每个策略 ≤3 个核心因子；必须通过 walk-forward 验证才能上线；引入在线学习微调参数
- **理由**：过度优化 + 静态参数 = 过拟合。简单策略 + 在线适应 + 严格验证 = 长期存活
- **反模式**：用越来越复杂的策略补偿过拟合（恶化问题）

### ADR-4：数据不在 git 中，用脚本重建

- **决策**：`data/` 目录全部 `.gitignore`；每台机器通过 JQ 同步脚本独立获取数据
- **理由**：SQLite 文件大、二进制、不适合 git diff；JQ 数据源统一，每台机器拉取结果一致
- **例外**：`data/meta/` 中的成分股快照可以 git（体积小、文本化、变更少）

---

## 六、实施进度（2025-07-30）

### ✅ Phase 0：加固当前架构

| 任务 | 状态 |
|------|------|
| .gitignore 完善 | ✅ 已初始化 git 仓库，156 文件提交，data/ 和 .env 排除 |
| 系统健康验证 | ✅ `final_freeze_2025.toml` 回测通过：年化 39.8%，超额 +10.2%，Sharpe 2.02 |
| quota_blocked | ⏸ JQ 配额问题，非代码问题，需在交易日验证 |

### ✅ Phase 1：引入 GitHub Actions

| 任务 | 状态 |
|------|------|
| `.github/workflows/backtest_check.yml` | ✅ 每日回测 + 漂移告警，push + schedule 触发 |
| `.github/workflows/factor_analysis.yml` | ✅ 每周因子 IC/IR 分析，周六运行 |
| `.github/workflows/data_sync.yml` | ✅ 手动触发 mode consistency + JQ 同步 |
| `.github/workflows/jq_signin.yml` | ✅ 每日聚宽自动签到领积分（Playwright + 验证码识别） |
| 数据外置 | ✅ `data/` → symlink → `~/HHD/stock/data/`（机械硬盘） |

### ✅ 纸盘验证

| 步骤 | 状态 |
|------|------|
| 纸盘配置 | ✅ `config/live_paper.toml`：broker_type=paper, dry_run=true |
| Pipeline 端到端 | ✅ `run_daily_live_pipeline.sh` 跑通（prepare-live-plan + archive） |
| .gitignore | ✅ data/、output/*、.env、uv.lock 已排除 |

### ✅ Phase 2：策略演进能力

| 任务 | 模块 | 说明 |
|------|------|------|
| walk-forward 回测框架 | `quant/tools/walk_forward.py` | 滚动窗口训练/验证，超参数优化（top_k、止损、入场间隔），过拟合检测（参数稳定性 + 超额持续性） |
| 简化策略 | `quant/stock_strategy/simple_3factor.py` | 3 因子策略（基本面 + 动量 + 稳定性）和 2 因子基线（质量 + 动量），已注册到 registry |
| 在线参数更新 | `quant/stock_strategy/online_learning.py` | EGD 算法，基于 rank IC 梯度每日微调因子权重，持久化到 JSON |
| 策略生命周期 | `quant/stock_strategy/lifecycle.py` | active → watch → retired 三级状态机，基于滚动 Sharpe/超额/回撤自动退役 |

### ⏳ 待完成

- [ ] MiniQMT 网关在 Windows 实机联调（需开通券商量化账号）
- [ ] `live_qmt_http.toml` 填入 gateway token 后切到实盘纸盘模式
- [ ] GitHub Actions 首次运行验证（观察 jq_signin 是否正常）
- [ ] ML 异常检测模块
- [ ] NLP 财报分析 Agent (hermes)
- [ ] 数据闭环 MLOps

---

## 七、下一步行动

1. [ ] **开通券商量化账号**（国金证券，QMT/MiniQMT）
2. [ ] Windows 实机安装 MiniQMT + 启动 `xtquant_gateway`
3. [ ] 将 `live_qmt_http.toml` 的 `broker_type` 改为 `qmt_http`，填入 token
4. [ ] 在 GitHub 上观察 `jq_signin` workflow 首次运行结果
5. [ ] 每周 `git pull` 同步代码 + 策略更新
