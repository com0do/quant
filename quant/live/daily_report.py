from __future__ import annotations

from pathlib import Path
import json


def generate_daily_report(day: str, account: dict, positions: dict[str, int], events_file: str | None = None) -> str:
    Path("reports").mkdir(exist_ok=True)
    lines = [
        f"# Daily Report {day}",
        "",
        f"- cash: {account.get('cash', 0)}",
        f"- equity: {account.get('equity', 0)}",
        f"- position_count: {len(positions)}",
    ]
    if events_file:
        lines.append(f"- events_file: {events_file}")
        try:
            data = json.loads(Path(events_file).read_text(encoding="utf-8"))
            lines.append(f"- events: {len(data)}")
        except Exception:
            pass
    out = Path(f"reports/daily_report_{day}.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
