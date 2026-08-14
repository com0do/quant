# LLM 一次性调用层

**定位**：文本进 → 结构化 JSON 出。**无状态、无工具、无记忆**。

**跟 `quant/ml/hermes/` 的区别**：

| | LLM 层（本目录） | Hermes agent 层 |
|---|---|---|
| 场景 | 批量结构化提取（公告→JSON） | 多步推理、要查库、要记忆 |
| 输入 | 一段文本 | 一个任务描述 |
| 工具调用 | 无 | 有（`get_kbars` / `get_fundamentals` …） |
| 会话 | 单次 | 多轮 |
| 成本 | 每次一 token | 每次可能多 token（工具循环） |
| 后端 | 直连 DeepSeek HTTP | 走 hermes-agent runtime |

## 快速上手

### 1) 配 DeepSeek

复制 `.env.example` 为 `.env`，填：

```
DEEPSEEK_API_KEY=sk-xxx           # 必填
DEEPSEEK_MODEL=deepseek-chat      # 默认 deepseek-chat；推理场景可切 deepseek-reasoner
DEEPSEEK_BASE_URL=https://api.deepseek.com   # 官方；也可硅基流动 / 自建
```

### 2) 准备公告 JSONL

`data/nlp/inbox/*.jsonl`，一行一份：

```jsonl
{"code":"000001.XSHE","date":"2025-01-15","title":"业绩预告","text":"公司预计 2024 年营业收入 ..."}
{"code":"600519.XSHG","date":"2025-01-16","title":"股份回购公告","text":"公司拟以自有资金 ..."}
```

### 3) 跑

```bash
uv run python main.py --mode nlp-analyze --dry-run     # 冒烟，不调 API
uv run python main.py --mode nlp-analyze               # 实跑
uv run python main.py --mode nlp-analyze --limit 20    # 限量
```

产物：`data/nlp/signals.db` 的 `nlp_signals` 表；`source_text_hash` 做幂等。

## 表结构

```sql
CREATE TABLE nlp_signals (
    source_text_hash TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    title TEXT,
    category TEXT,           -- earnings/major_event/holding/governance/guarantee/risk/other
    sentiment TEXT,          -- bullish/bearish/neutral
    sentiment_score REAL,    -- -1 ~ +1
    revenue_yoy REAL,
    net_profit_yoy REAL,
    risk_flags TEXT,         -- JSON list
    summary TEXT,
    confidence REAL,
    generated_by TEXT,       -- 记录用的 DeepSeek 模型名
    generated_at TEXT,
    raw_error TEXT
);
```

## 降级模式

- `.env` 缺 `DEEPSEEK_API_KEY` / 端点不可达（`ping()` 失败）：整个批次直接返回，不写库。
- 单条解析失败：写入 neutral 记录，`raw_error` 保留错误信息，主流程不中断。
- 幂等：`source_text_hash` 主键；已处理的公告不会再次调用 API。

## 与因子链路的对接（下一步）

- 在 `quant/tools/factor_analyze.py` 中加载 `nlp_signals`，与量价因子一起做 IC/IR。
- 显著的字段（例如 `sentiment_score` × `confidence`）经过 walk-forward 验证后纳入 `simple_3factor` / `gbm_multifactor` 的特征。

## 局限

- **幻觉**：LLM 可能编造数字。**任何入池信号必须过 walk-forward IC 检验**。
- **上下文长度**：长报告被截断到 30k 字符；超长报告需要用 RAG 分块（本模块未实现，走 Hermes agent + `announcements` 工具再迭代）。
- **单轮**：如果需要"先查历史财报再对比这次的"，用不了 LLM 层，必须走 Hermes agent。
