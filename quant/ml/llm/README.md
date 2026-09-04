# One-shot LLM call layer

**Positioning**: text in → structured JSON out. **Stateless, no tools, no memory**.

**Difference from `quant/ml/hermes/`**:

| | LLM layer (this directory) | Hermes agent layer |
|---|---|---|
| Use case | Batch structured extraction (announcement→JSON) | Multi-step reasoning, DB lookups, memory |
| Input | A piece of text | A task description |
| Tool calls | None | Yes (`get_kbars` / `get_fundamentals` …) |
| Session | Single-shot | Multi-turn |
| Cost | One round of token cost per call | Possibly many tokens per call (tool loop) |
| Backend | Direct DeepSeek HTTP | Goes through the hermes-agent runtime |

## Quick start

### 1) Configure DeepSeek

Copy `.env.example` to `.env` and fill in:

```
DEEPSEEK_API_KEY=sk-xxx           # required
DEEPSEEK_MODEL=deepseek-chat      # default deepseek-chat; switch to deepseek-reasoner for reasoning scenarios
DEEPSEEK_BASE_URL=https://api.deepseek.com   # official; SiliconFlow / self-hosted also work
```

### 2) Prepare the announcement JSONL

`data/nlp/inbox/*.jsonl`, one announcement per line:

```jsonl
{"code":"000001.XSHE","date":"2025-01-15","title":"Earnings forecast","text":"The company expects 2024 operating revenue ..."}
{"code":"600519.XSHG","date":"2025-01-16","title":"Share buyback announcement","text":"The company plans to use its own funds ..."}
```

### 3) Run

```bash
uv run python main.py --mode nlp-analyze --dry-run     # smoke test, no API call
uv run python main.py --mode nlp-analyze               # real run
uv run python main.py --mode nlp-analyze --limit 20    # limit the count
```

Output: the `nlp_signals` table in `data/nlp/signals.db`; `source_text_hash` provides idempotency.

## Table schema

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
    generated_by TEXT,       -- records the DeepSeek model name used
    generated_at TEXT,
    raw_error TEXT
);
```

## Degraded mode

- `.env` lacks `DEEPSEEK_API_KEY` / endpoint unreachable (`ping()` fails): the whole batch returns immediately without writing to the DB.
- A single parse failure: a neutral record is written, `raw_error` keeps the error message, and the main flow is not interrupted.
- Idempotency: `source_text_hash` is the primary key; announcements already processed will not call the API again.

## Integration with the factor pipeline (next step)

- Load `nlp_signals` in `quant/tools/factor_analyze.py` and run IC/IR together with the price-volume factors.
- Significant fields (e.g. `sentiment_score` × `confidence`) are admitted as features of `simple_3factor` / `gbm_multifactor` after walk-forward validation.

## Limitations

- **Hallucination**: the LLM may fabricate numbers. **Any signal admitted to the pool must pass a walk-forward IC test**.
- **Context length**: long reports are truncated to 30k characters; very long reports need RAG chunking (not implemented in this module; to be iterated via the Hermes agent + the `announcements` tool).
- **Single round**: if you need to "first look up historical financial reports and then compare against this one", the LLM layer can't do it; you must go through the Hermes agent.
