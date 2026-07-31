#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import uvicorn

from xtquant_gateway import GatewaySettings, create_app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run xtquant gateway service.")
    p.add_argument(
        "--env-file",
        default="",
        help="Optional .env file path for XTG_* settings. Defaults to deploy/gateway_server/.env if present.",
    )
    p.add_argument(
        "--autostart-miniqmt",
        action="store_true",
        help="Try to start miniQMT executable before gateway.",
    )
    return p.parse_args()


def _autostart_miniqmt(cfg: GatewaySettings) -> None:
    exe = str(getattr(cfg, "mini_qmt_exe_path", "") or "").strip()
    if not exe:
        p0 = str(getattr(cfg, "mini_qmt_path", "") or "").strip()
        if p0.lower().endswith(".exe"):
            exe = p0
    if not exe:
        return
    p = Path(exe)
    if not p.exists():
        return
    # Detached startup so miniQMT survives parent terminal/session exit on Windows.
    kwargs: dict = {"cwd": str(p.parent)}
    if hasattr(subprocess, "DETACHED_PROCESS") and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        kwargs["creationflags"] = int(subprocess.DETACHED_PROCESS) | int(subprocess.CREATE_NEW_PROCESS_GROUP)
        kwargs["close_fds"] = True
    subprocess.Popen([str(p)], **kwargs)


def main() -> int:
    args = parse_args()
    cfg = GatewaySettings.from_env(env_file=args.env_file)
    if args.autostart_miniqmt:
        _autostart_miniqmt(cfg)
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
