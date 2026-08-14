"""LLM 一次性调用层：文本进 → 结构化 JSON 出。

设计原则：
- 只做**无状态、无工具、无记忆**的一次性调用。
- 需要多步推理 / 用工具查数据 / 会话记忆的场景走 `quant.ml.hermes` 而不是这里。
- 只依赖 `requests`（已在主 pyproject 里）。DeepSeek key 走 `.env`。

推荐用法：
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
