"""Build a compact daily news/policy digest for a Hermes session.

Example:
  uv run python research/news_selector/digest.py --date 2026-10-26 > /tmp/digest.md
  uv run python main.py --mode hermes-chat --prompt "$(cat /tmp/digest.md)"

The digest is a prompt, not an order: Hermes (or a human) reads the titles, checks the themes and decides whether
any of them deserves a deeper look. The structured selector is `selector.py`; this script is the qualitative layer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "news_selector"))

from classify import classify_announcements_frame, classify_limit_up_reason, classify_policy_frame  # noqa: E402
from quant.stock_data.news_store import NewsStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "news" / "news.db"))
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    store = NewsStore(args.db)
    day = args.date

    print(f"# Daily A-share news digest — {day}\n")
    print("Use the items below as hypotheses, not orders. Check the source, the company's main business and whether "
          "the news is already priced in. Respond in Chinese.\n")

    pol = store.policy_news(day, day)
    if not pol.empty:
        pol = classify_policy_frame(pol)
        pol = pol[pol["score"] > 0].head(args.top)
        print("## Policy / macro\n")
        for r in pol.to_dict("records"):
            print(f"- **{r['title']}** — themes: {', '.join(r.get('themes') or [])}")
        print()

    ann = store.announcements(day, day)
    if not ann.empty:
        ann = classify_announcements_frame(ann)
        pos = ann[ann["score"] > 0].sort_values("score", ascending=False).head(args.top)
        neg = ann[ann["score"] < 0].sort_values("score").head(args.top)
        print("## Positive announcements\n")
        for r in pos.to_dict("records"):
            print(f"- {r['code']} {r.get('name', '')}: {r['title']}")
        print("\n## Negative announcements (avoid / check)\n")
        for r in neg.to_dict("records"):
            print(f"- {r['code']} {r.get('name', '')}: {r['title']}")
        print()

    lu = store.limit_up(day, day)
    if not lu.empty:
        print("## Limit-up themes\n")
        themes = {}
        for r in lu.to_dict("records"):
            for tag in classify_limit_up_reason(r.get("reason", "")):
                themes[tag] = themes.get(tag, 0) + 1
        for tag, n in sorted(themes.items(), key=lambda x: -x[1])[:20]:
            print(f"- {tag}: {n}")
        print()

    print("## Questions for the analyst\n")
    print("1. Which of the positive items are genuinely new information, and which are routine?")
    print("2. Which themes have a policy tailwind but no crowded limit-up cluster yet?")
    print("3. Is the technical trend of the relevant names already extended?")


if __name__ == "__main__":
    main()
