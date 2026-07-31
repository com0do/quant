from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time


def run_mode_consistency(config_path: str | None = None) -> dict:
    Path("reports").mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)
    modes = [
        "backtest",
        "daily-report",
        "factor-analyze",
        "vectorbt-optimize",
        "solidify-params",
        "sync-plan",
        "optimize",
        "scan-optimize",
        "iterative-optimize",
        "profile-scan",
        "prefetch",
        "jq-bulk-sync",
        "live-daemon",
        "pickle-to-db",
        "cleanup-pickle",
    ]
    env = os.environ.copy()
    env["OPT_QUICK"] = "1"
    env["SCAN_OPT_QUICK"] = "1"
    env["ITERATIVE_QUICK"] = "1"

    rows = []
    skip_network = os.getenv("MODE_CHECK_SKIP_NETWORK", "1") == "1"
    network_modes = {"prefetch", "jq-bulk-sync"}
    for m in modes:
        if skip_network and m in network_modes:
            rows.append(
                {
                    "mode": m,
                    "status": "skipped",
                    "returncode": 0,
                    "elapsed_sec": 0.0,
                    "stdout_tail": "",
                    "stderr_tail": "skipped by MODE_CHECK_SKIP_NETWORK=1",
                }
            )
            continue
        cmd = ["uv", "run", "python", "main.py", "--mode", m]
        if config_path:
            cmd += ["--config", config_path]
        t0 = time.time()
        try:
            cp = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=180 if m not in {"jq-bulk-sync", "prefetch"} else 240,
            )
            code = int(cp.returncode)
            out = (cp.stdout or "")[-1000:]
            err = (cp.stderr or "")[-1000:]
            status = "ok" if code == 0 else "failed"
        except Exception as exc:
            code = -1
            out = ""
            err = str(exc)
            status = "failed"
        elapsed = round(time.time() - t0, 2)
        if status == "ok" and m == "prefetch":
            rp = Path("output/prefetch_report.json")
            if rp.exists():
                try:
                    info = json.loads(rp.read_text(encoding="utf-8"))
                    if info.get("status") == "quota_blocked":
                        status = "quota_blocked"
                except Exception:
                    pass
        if status == "ok" and m == "jq-bulk-sync":
            rp = Path("output/jq_bulk_sync_report.json")
            if rp.exists():
                try:
                    info = json.loads(rp.read_text(encoding="utf-8"))
                    if info.get("status") == "quota_blocked":
                        status = "quota_blocked"
                except Exception:
                    pass
        rows.append(
            {
                "mode": m,
                "status": status,
                "returncode": code,
                "elapsed_sec": elapsed,
                "stdout_tail": out,
                "stderr_tail": err,
            }
        )

    result = {
        "config_path": config_path,
        "summary": {
            "total": len(rows),
            "ok": sum(1 for x in rows if x["status"] == "ok"),
            "skipped": sum(1 for x in rows if x["status"] == "skipped"),
            "quota_blocked": sum(1 for x in rows if x["status"] == "quota_blocked"),
            "failed": sum(1 for x in rows if x["status"] == "failed"),
        },
        "rows": rows,
    }
    Path("output/mode_consistency_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = [
        "# Mode Consistency Report",
        "",
        f"- total modes: {result['summary']['total']}",
        f"- ok: {result['summary']['ok']}",
        f"- skipped: {result['summary']['skipped']}",
        f"- quota_blocked: {result['summary']['quota_blocked']}",
        f"- failed: {result['summary']['failed']}",
        "",
        "## Per Mode",
    ]
    for r in rows:
        md.append(f"- `{r['mode']}`: {r['status']} (rc={r['returncode']}, {r['elapsed_sec']}s)")
    Path("reports/mode_consistency_report.md").write_text("\n".join(md), encoding="utf-8")
    return result
