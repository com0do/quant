from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def run_solidify_params() -> dict:
    out = {}
    p = Path("output/scan_optimization_results.csv")
    if p.exists():
        df = pd.read_csv(p)
        if not df.empty:
            df = df.sort_values(["excess_ann_return", "score"], ascending=False)
            out["scan_best"] = df.iloc[0].to_dict()
    p2 = Path("output/vectorbt_optimization.csv")
    if p2.exists():
        df2 = pd.read_csv(p2)
        if not df2.empty:
            out["vectorbt_best"] = df2.sort_values("score", ascending=False).iloc[0].to_dict()
    Path("output").mkdir(exist_ok=True)
    Path("output/solidified_params_report.md").write_text(
        "# Solidified Params\n\n```json\n" + json.dumps(out, ensure_ascii=False, indent=2) + "\n```",
        encoding="utf-8",
    )
    return out
