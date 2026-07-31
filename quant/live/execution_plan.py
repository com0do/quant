from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExecutionPlan:
    plan_date: str
    generated_at: str
    source_config_path: str
    benchmark_index: str
    watchlist: list[str]
    backtest_metrics: dict[str, Any]
    strategy_snapshot: dict[str, Any]


def save_execution_plan(path: str, plan: ExecutionPlan) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def load_execution_plan(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
