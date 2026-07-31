from __future__ import annotations

from pathlib import Path

from quant.config import load_config


def run_sync_plan(config_path: str | None = None) -> str:
    cfg = load_config(config_path=config_path)
    text = (
        "# Tomorrow Sync Plan\n\n"
        f"- source: {cfg.data.source}\n"
        f"- db: {cfg.data.sqlite_db_path}\n"
        f"- date_range: {cfg.data.start_date} ~ {cfg.data.end_date}\n"
        f"- benchmark_index: {cfg.data.benchmark_index}\n"
        f"- jq_sync_daily_row_budget: {cfg.data.jq_sync_daily_row_budget}\n"
    )
    Path("output").mkdir(exist_ok=True)
    out = Path("output/tomorrow_sync_plan.md")
    out.write_text(text, encoding="utf-8")
    return str(out)
