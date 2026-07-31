from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass
class ArchiveWriter:
    base_dir: str = "reports/archive"
    events: list[dict] = field(default_factory=list)

    def record(self, event: dict) -> None:
        self.events.append(event)

    def flush(self, day: str) -> str:
        p = Path(self.base_dir)
        p.mkdir(parents=True, exist_ok=True)
        f = p / f"events_{day}.json"
        f.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(f)
