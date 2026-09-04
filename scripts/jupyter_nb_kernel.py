#!/usr/bin/env python3
"""Point the metadata.kernelspec of .ipynb files at the project kernel.

Only the three kernelspec fields change; everything else keeps the original indentation and line endings,
so the git diff is two or three lines. Standard library only. Called by `make nb-kernel` in the root
Makefile, and can also be run on its own:

    python scripts/jupyter_nb_kernel.py --name quant --display "quant (.venv)" test/*.ipynb
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _indent_of(raw: str) -> int:
    lines = raw.split("\n", 2)
    if len(lines) < 2:
        return 1
    return len(lines[1]) - len(lines[1].lstrip()) or 1


def fix(path: Path, name: str, display: str) -> bool:
    raw = path.read_text(encoding="utf-8")
    nb = json.loads(raw)
    want = {"name": name, "display_name": display, "language": "python"}
    ks = nb.setdefault("metadata", {}).setdefault("kernelspec", {})
    if all(ks.get(k) == v for k, v in want.items()):
        return False
    ks.update(want)
    path.write_text(
        json.dumps(nb, ensure_ascii=False, indent=_indent_of(raw)) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", required=True, help="kernelspec name (= pyproject project name)")
    ap.add_argument("--display", required=True, help="kernelspec display name")
    ap.add_argument("notebooks", nargs="*", type=Path)
    args = ap.parse_args()

    if not args.notebooks:
        print("No notebooks found")
        return 0
    for p in args.notebooks:
        print(("patched " if fix(p, args.name, args.display) else "ok      ") + str(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
