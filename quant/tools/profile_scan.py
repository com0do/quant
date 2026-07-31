from __future__ import annotations

import cProfile
import pstats
from pathlib import Path

from quant.tools.scan_optimize import run_scan_optimize


def run_profile_scan() -> str:
    Path("output").mkdir(exist_ok=True)
    prof = cProfile.Profile()
    prof.enable()
    run_scan_optimize(workers=4)
    prof.disable()
    out = "output/scan_profile.txt"
    with open(out, "w", encoding="utf-8") as f:
        stats = pstats.Stats(prof, stream=f)
        stats.sort_stats("cumtime")
        stats.print_stats(60)
    return out
