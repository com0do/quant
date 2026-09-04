"""Structured schema for announcements.

One announcement / financial-report input -> one AnnouncementSignal output, persisted to SQLite.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


CATEGORIES = {
    "earnings",
    "major_event",
    "holding",
    "governance",
    "guarantee",
    "risk",
    "other",
}

SENTIMENTS = {"bullish", "bearish", "neutral"}

# The vocabulary is deliberately Chinese: the values are matched literally against the LLM output
# (see prompts/announcement_zh.md) and stored as-is, and the source announcements are Chinese.
RISK_FLAG_VOCAB = [
    "减持", "增持", "问询函", "商誉减值", "经营异常", "诉讼",
    "高管变动", "停牌", "退市风险", "关联交易", "股份回购", "再融资",
    "业绩预增", "业绩预减", "违规担保", "资产重组",
]


@dataclass
class AnnouncementInput:
    """The raw text of one announcement / report to analyze."""
    code: str
    date: str
    title: str
    text: str

    def source_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.code.encode())
        h.update(self.date.encode())
        h.update(self.title.encode())
        h.update(self.text.encode())
        return h.hexdigest()


@dataclass
class AnnouncementSignal:
    """Structured signal extracted by the LLM. Fields are strictly aligned with the prompt."""
    code: str
    date: str
    title: str
    source_text_hash: str
    category: str = "other"
    sentiment: str = "neutral"
    sentiment_score: float = 0.0
    revenue_yoy: float | None = None
    net_profit_yoy: float | None = None
    risk_flags: list[str] = field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0
    generated_by: str = ""
    generated_at: str = ""
    raw_error: str | None = None

    @classmethod
    def neutral(cls, ann: AnnouncementInput, generated_by: str = "fallback", err: str | None = None) -> "AnnouncementSignal":
        return cls(
            code=ann.code,
            date=ann.date,
            title=ann.title,
            source_text_hash=ann.source_hash(),
            category="other",
            sentiment="neutral",
            sentiment_score=0.0,
            confidence=0.0,
            generated_by=generated_by,
            raw_error=err,
        )

    @classmethod
    def from_llm_json(
        cls,
        ann: AnnouncementInput,
        parsed: dict[str, Any],
        generated_by: str,
    ) -> "AnnouncementSignal":
        cat = str(parsed.get("category", "other")).strip()
        if cat not in CATEGORIES:
            cat = "other"
        sent = str(parsed.get("sentiment", "neutral")).strip()
        if sent not in SENTIMENTS:
            sent = "neutral"

        risk = parsed.get("risk_flags") or []
        if not isinstance(risk, list):
            risk = []
        risk = [str(x) for x in risk if str(x) in RISK_FLAG_VOCAB]

        def _f(v: Any) -> float | None:
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _clip(v: Any, lo: float, hi: float, default: float) -> float:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, x))

        return cls(
            code=ann.code,
            date=ann.date,
            title=ann.title,
            source_text_hash=ann.source_hash(),
            category=cat,
            sentiment=sent,
            sentiment_score=_clip(parsed.get("sentiment_score", 0.0), -1.0, 1.0, 0.0),
            revenue_yoy=_f(parsed.get("revenue_yoy")),
            net_profit_yoy=_f(parsed.get("net_profit_yoy")),
            risk_flags=risk,
            summary=str(parsed.get("summary", ""))[:400],
            confidence=_clip(parsed.get("confidence", 0.0), 0.0, 1.0, 0.0),
            generated_by=generated_by,
        )

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk_flags"] = json.dumps(self.risk_flags, ensure_ascii=False)
        return d
