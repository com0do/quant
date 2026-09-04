"""One-shot LLM call layer: text in -> structured JSON out.

Design principles:
- Only **stateless, tool-less, memory-less** one-shot calls.
- Anything needing multi-step reasoning / tools to look up data / conversation memory goes through `quant.ml.hermes`, not here.
- Depends only on `requests` (already in the main pyproject). The DeepSeek key comes from `.env`.

Recommended usage:
    from quant.ml.llm import DeepSeekClient
    from quant.ml.llm.announcement_pipeline import run_batch

    run_batch(input_glob="data/nlp/inbox/*.jsonl", db_path="data/nlp/signals.db")
"""

from quant.ml.llm.deepseek import DeepSeekClient, DeepSeekSettings, DeepSeekUnavailableError
from quant.ml.llm.schema import AnnouncementInput, AnnouncementSignal

__all__ = [
    "DeepSeekClient",
    "DeepSeekSettings",
    "DeepSeekUnavailableError",
    "AnnouncementInput",
    "AnnouncementSignal",
]
