"""批量分析公告：读 JSONL 输入 → 直连 DeepSeek → 写 SQLite。

输入 JSONL 示例（一行一份公告）：
    {"code": "000001.XSHE", "date": "2025-01-15", "title": "...", "text": "..."}

输出 SQLite：
    表 nlp_signals，主键 source_text_hash（同一条公告不重复处理）。

设计要点：
- 无状态、无工具、无记忆——这是 LLM 一次性调用，不是 agent。
- 端点不可达 / 单条解析失败：写 raw_error，不打断整个批次。
- text 太长时截断到 max_text_chars（默认 30000）。
- 通过 source_text_hash 做幂等，重复运行不会重复消耗 token。
"""

from __future__ import annotations

import glob
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from quant.ml.llm.deepseek import DeepSeekClient, DeepSeekSettings, DeepSeekUnavailableError
from quant.ml.llm.schema import AnnouncementInput, AnnouncementSignal


_PROMPT_PATH = Path(__file__).parent / "prompts" / "announcement_zh.md"


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _render_prompt(tpl: str, ann: AnnouncementInput, max_text_chars: int) -> str:
    text = ann.text or ""
    if len(text) > max_text_chars:
        text = text[:max_text_chars] + "\n...[truncated]"
    return (
        tpl.replace("{TITLE}", ann.title or "")
        .replace("{CODE}", ann.code or "")
        .replace("{DATE}", ann.date or "")
        .replace("{TEXT}", text)
    )


def _extract_json(text: str) -> dict | None:
    """从模型输出里稳健提取一个 JSON 对象。允许存在 markdown 围栏或多余文字。"""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    snippet = m.group(0)
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


def _ensure_schema(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nlp_signals (
                source_text_hash TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                title TEXT,
                category TEXT,
                sentiment TEXT,
                sentiment_score REAL,
                revenue_yoy REAL,
                net_profit_yoy REAL,
                risk_flags TEXT,
                summary TEXT,
                confidence REAL,
                generated_by TEXT,
                generated_at TEXT,
                raw_error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nlp_signals_code_date ON nlp_signals(code, date)")


def _row_exists(conn: sqlite3.Connection, source_hash: str) -> bool:
    cur = conn.execute("SELECT 1 FROM nlp_signals WHERE source_text_hash=? LIMIT 1", (source_hash,))
    return cur.fetchone() is not None


def _write_signal(conn: sqlite3.Connection, sig: AnnouncementSignal) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO nlp_signals (
            source_text_hash, code, date, title,
            category, sentiment, sentiment_score,
            revenue_yoy, net_profit_yoy, risk_flags,
            summary, confidence, generated_by, generated_at, raw_error
        ) VALUES (
            :source_text_hash, :code, :date, :title,
            :category, :sentiment, :sentiment_score,
            :revenue_yoy, :net_profit_yoy, :risk_flags,
            :summary, :confidence, :generated_by, :generated_at, :raw_error
        )
        """,
        sig.to_row(),
    )


def _iter_announcements(input_glob: str) -> Iterator[AnnouncementInput]:
    for path in sorted(glob.glob(input_glob)):
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    yield AnnouncementInput(
                        code=str(row["code"]).strip(),
                        date=str(row["date"]).strip(),
                        title=str(row.get("title", "")).strip(),
                        text=str(row.get("text", "")),
                    )
                except (KeyError, ValueError, TypeError) as e:
                    print(f"[llm-pipeline] skip {path}:{line_no}: {e}")


def run_batch(
    input_glob: str = "data/nlp/inbox/*.jsonl",
    db_path: str = "data/nlp/signals.db",
    limit: int = 0,
    dry_run: bool = False,
    max_text_chars: int = 30000,
    settings: DeepSeekSettings | None = None,
) -> dict:
    """处理一批公告，返回统计信息。

    dry_run=True：走完流水线但不真正调用 DeepSeek（用于本地冒烟）。
    limit>0：本次最多处理多少条（超过就停）。
    """
    stats = {
        "input_files": 0,
        "seen": 0,
        "skipped_dedup": 0,
        "written": 0,
        "errors": 0,
        "unavailable": False,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    stats["input_files"] = len(glob.glob(input_glob))

    _ensure_schema(db_path)
    template = _load_prompt_template()
    client: DeepSeekClient | None = None
    generated_by = "dry-run"

    if not dry_run:
        client = DeepSeekClient(settings or DeepSeekSettings.from_env())
        if not client.ping():
            stats["unavailable"] = True
            stats["ended_at"] = datetime.now().isoformat(timespec="seconds")
            print("[llm-pipeline] DeepSeek endpoint unavailable, nothing done. "
                  "Set DEEPSEEK_API_KEY (and optionally DEEPSEEK_BASE_URL / DEEPSEEK_MODEL) and retry.")
            return stats
        generated_by = client.settings.model

    with sqlite3.connect(db_path) as conn:
        for ann in _iter_announcements(input_glob):
            stats["seen"] += 1
            source_hash = ann.source_hash()
            if _row_exists(conn, source_hash):
                stats["skipped_dedup"] += 1
                continue

            if dry_run:
                sig = AnnouncementSignal.neutral(ann, generated_by="dry-run")
                _write_signal(conn, sig)
                stats["written"] += 1
            else:
                assert client is not None
                prompt = _render_prompt(template, ann, max_text_chars)
                messages = [
                    {"role": "system", "content": "你是严格输出 JSON 的助手。"},
                    {"role": "user", "content": prompt},
                ]
                try:
                    reply = client.chat(messages, json_mode=True)
                    parsed = _extract_json(reply)
                    if parsed is None:
                        raise ValueError("parse-json-failed")
                    sig = AnnouncementSignal.from_llm_json(ann, parsed, generated_by=generated_by)
                    sig.generated_at = datetime.now().isoformat(timespec="seconds")
                    _write_signal(conn, sig)
                    stats["written"] += 1
                except (DeepSeekUnavailableError, ValueError) as e:
                    sig = AnnouncementSignal.neutral(ann, generated_by=generated_by, err=str(e)[:200])
                    sig.generated_at = datetime.now().isoformat(timespec="seconds")
                    _write_signal(conn, sig)
                    stats["errors"] += 1

            if limit and (stats["written"] + stats["errors"]) >= limit:
                break

            time.sleep(0)
        conn.commit()

    stats["ended_at"] = datetime.now().isoformat(timespec="seconds")
    return stats
