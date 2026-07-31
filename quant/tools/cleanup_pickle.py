from __future__ import annotations

from pathlib import Path


def run_cleanup_pickle(root: str = "cache") -> int:
    n = 0
    for p in Path(root).rglob("*.pkl"):
        p.unlink(missing_ok=True)
        n += 1
    return n
