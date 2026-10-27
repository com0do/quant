"""Optional LLM pass over news / announcements, using the existing `quant/ml/llm` DeepSeek client.

The rule classifier (`classify.py`) is the default because a five-month backtest classifies ~200k headlines. This
script is the quality path: run it on the headlines that matter (new policy items, major-event announcements) and
write structured signals into `news_signals`. It degrades to a no-op when `DEEPSEEK_API_KEY` is absent.

usage:
  uv run python research/news_selector/llm_classify.py --kind ann --limit 20 --dry-run
  DEEPSEEK_API_KEY=... uv run python research/news_selector/llm_classify.py --kind policy --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.ml.llm.deepseek import DeepSeekClient, DeepSeekSettings, DeepSeekUnavailableError  # noqa: E402
from quant.stock_data.news_store import NewsStore  # noqa: E402

PROMPT = Path(__file__).parent / "prompts" / "news_zh.md"


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _row_id(source: str, date: str, code: str, title: str) -> str:
    return hashlib.sha256(f"{source}|{date}|{code}|{title}".encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["ann", "policy"], default="ann")
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="2026-10-26")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "data" / "news" / "news.db"))
    args = ap.parse_args()

    store = NewsStore(args.db)
    if args.kind == "ann":
        df = store.announcements(args.start, args.end).head(args.limit)
        rows = [{"source": "announcement", "date": r["date"], "code": r["code"], "title": r["title"],
                 "content": f"{r.get('type', '')}"} for r in df.to_dict("records")]
    else:
        df = store.policy_news(args.start, args.end).head(args.limit)
        rows = [{"source": "policy", "date": r["date"], "code": "", "title": r["title"],
                 "content": r.get("content", "")} for r in df.to_dict("records")]
    print(f"[llm] {len(rows)} rows, kind={args.kind}, dry_run={args.dry_run}")

    client = None
    if not args.dry_run:
        settings = DeepSeekSettings.from_env()
        if not settings.api_key:
            print("[llm] DEEPSEEK_API_KEY is not set; nothing done. Use --dry-run for the local smoke test.")
            return
        client = DeepSeekClient(settings)
        if not client.ping():
            print("[llm] DeepSeek endpoint unavailable; nothing done.")
            return

    template = PROMPT.read_text(encoding="utf-8")
    out = []
    for row in rows:
        rid = _row_id(row["source"], row["date"], row["code"], row["title"])
        if args.dry_run:
            sig = {"sentiment": "neutral", "sentiment_score": 0.0, "category": "other",
                   "themes": [], "summary": "", "confidence": 0.0}
            method = "dry-run"
        else:
            prompt = (template.replace("{SOURCE}", row["source"]).replace("{DATE}", row["date"])
                      .replace("{CODE}", row["code"]).replace("{TITLE}", row["title"])
                      .replace("{CONTENT}", (row["content"] or "")[:12000]))
            try:
                assert client is not None
                reply = client.chat([{"role": "system", "content": "Output strict JSON only."},
                                     {"role": "user", "content": prompt}], json_mode=True)
                sig = _extract_json(reply) or {}
                method = client.settings.model
            except DeepSeekUnavailableError as exc:
                print(f"[llm] {row['title'][:40]}: {exc}")
                continue
        out.append({"id": rid, "date": row["date"], "code": row["code"], "source": row["source"],
                    "title": row["title"], "category": sig.get("category", "other"),
                    "sentiment": sig.get("sentiment", "neutral"), "score": sig.get("sentiment_score", 0.0),
                    "themes": sig.get("themes", []), "confidence": sig.get("confidence", 0.0),
                    "method": method})
    print(f"[llm] wrote {store.upsert_signals(out)} signals")
    print("[llm] sample:", json.dumps(out[:2], ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()
