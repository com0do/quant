#!/usr/bin/env python3
from __future__ import annotations

import argparse

from quant.app import (
    run_backtest_app,
    run_cleanup_pickle_app,
    run_daily_report_app,
    run_factor_analyze_app,
    run_iterative_optimize_app,
    run_jq_bulk_sync_app,
    run_live_daemon_app,
    run_live_auto_app,
    run_mode_consistency_app,
    run_optimize_app,
    run_optimize_fast_app,
    run_pickle_to_db_app,
    run_prepare_live_plan_app,
    run_prefetch_app,
    run_profile_scan_app,
    run_scan_optimize_app,
    run_solidify_params_app,
    run_sync_plan_app,
    run_vectorbt_optimize_app,
    run_walk_forward_app,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quant trading system entry")
    p.add_argument(
        "--mode",
        default="backtest",
        choices=[
            "backtest",
            "live-daemon",
            "live-auto",
            "prepare-live-plan",
            "daily-report",
            "prefetch",
            "optimize",
            "optimize-fast",
            "scan-optimize",
            "iterative-optimize",
            "jq-bulk-sync",
            "pickle-to-db",
            "cleanup-pickle",
            "profile-scan",
            "factor-analyze",
            "vectorbt-optimize",
            "solidify-params",
            "sync-plan",
            "mode-consistency",
            "walk-forward",
        ],
    )
    p.add_argument("--config", default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "backtest":
        run_backtest_app(config_path=args.config)
    elif args.mode == "live-daemon":
        run_live_daemon_app(config_path=args.config)
    elif args.mode == "live-auto":
        run_live_auto_app(config_path=args.config)
    elif args.mode == "prepare-live-plan":
        run_prepare_live_plan_app(config_path=args.config)
    elif args.mode == "daily-report":
        run_daily_report_app()
    elif args.mode == "prefetch":
        run_prefetch_app(config_path=args.config)
    elif args.mode == "optimize":
        run_optimize_app(config_path=args.config)
    elif args.mode == "optimize-fast":
        run_optimize_fast_app(config_path=args.config)
    elif args.mode == "scan-optimize":
        run_scan_optimize_app(config_path=args.config)
    elif args.mode == "iterative-optimize":
        run_iterative_optimize_app(config_path=args.config)
    elif args.mode == "jq-bulk-sync":
        run_jq_bulk_sync_app(config_path=args.config)
    elif args.mode == "pickle-to-db":
        run_pickle_to_db_app()
    elif args.mode == "cleanup-pickle":
        run_cleanup_pickle_app()
    elif args.mode == "profile-scan":
        run_profile_scan_app()
    elif args.mode == "factor-analyze":
        run_factor_analyze_app(config_path=args.config)
    elif args.mode == "vectorbt-optimize":
        run_vectorbt_optimize_app(config_path=args.config)
    elif args.mode == "solidify-params":
        run_solidify_params_app()
    elif args.mode == "sync-plan":
        run_sync_plan_app(config_path=args.config)
    elif args.mode == "mode-consistency":
        run_mode_consistency_app(config_path=args.config)
    elif args.mode == "walk-forward":
        run_walk_forward_app(config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
