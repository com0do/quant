#!/usr/bin/env python3
from __future__ import annotations

import argparse

from quant.app import (
    run_backtest_app,
    run_cleanup_pickle_app,
    run_daily_report_app,
    run_factor_analyze_app,
    run_gbm_train_app,
    run_hermes_chat_app,
    run_iterative_optimize_app,
    run_jq_bulk_sync_app,
    run_live_daemon_app,
    run_live_auto_app,
    run_mode_consistency_app,
    run_nlp_analyze_app,
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
            "gbm-train",
            "nlp-analyze",
            "hermes-chat",
        ],
    )
    p.add_argument("--config", default=None)
    p.add_argument("--db-path", default=None, help="[gbm-train] override sqlite path")
    p.add_argument("--start-date", default="2023-01-01", help="[gbm-train / hermes-chat] window start")
    p.add_argument("--end-date", default="2025-06-30", help="[gbm-train / hermes-chat] window end")
    p.add_argument("--forward-days", type=int, default=5, help="[gbm-train] forward-return horizon")
    p.add_argument("--model-path", default="data/models/gbm_multifactor.txt", help="[gbm-train] output model path")
    p.add_argument("--input-glob", default="data/nlp/inbox/*.jsonl", help="[nlp-analyze] announcement JSONL glob")
    p.add_argument("--nlp-db", default="data/nlp/signals.db", help="[nlp-analyze] output sqlite path")
    p.add_argument("--limit", type=int, default=0, help="[nlp-analyze] max items per run (0 = unlimited)")
    p.add_argument("--dry-run", action="store_true", help="[nlp-analyze] don't call DeepSeek, only exercise the pipeline")
    p.add_argument("--prompt", default=None, help="[hermes-chat] free-form prompt to send to hermes-agent")
    p.add_argument("--skill", default=None, help="[hermes-chat] launch with a skill: analyst_zoe / info_charles / risk_kris / trader_ethan")
    p.add_argument("--code", default=None, help="[hermes-chat] target stock code, e.g. 000001.XSHE")
    p.add_argument("--codes", default=None, help="[hermes-chat] comma-separated stock codes (for info_charles / risk_kris)")
    p.add_argument("--date", default=None, help="[hermes-chat] asof date YYYY-MM-DD")
    p.add_argument("--profile", default="quant", help="[hermes-chat] hermes profile name under ~/.hermes/profiles/ (default: quant)")
    p.add_argument("--toolsets", default="core,quant", help="[hermes-chat] comma-separated hermes toolsets (default: core,quant)")
    p.add_argument("--model", default=None, help="[hermes-chat] override provider/model, e.g. deepseek/deepseek-chat")
    p.add_argument("--timeout-sec", type=int, default=600, help="[hermes-chat] max seconds to wait for hermes reply")
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
    elif args.mode == "gbm-train":
        run_gbm_train_app(
            config_path=args.config,
            db_path=args.db_path,
            start_date=args.start_date,
            end_date=args.end_date,
            forward_days=args.forward_days,
            model_path=args.model_path,
        )
    elif args.mode == "nlp-analyze":
        run_nlp_analyze_app(
            input_glob=args.input_glob,
            db_path=args.nlp_db,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    elif args.mode == "hermes-chat":
        run_hermes_chat_app(
            prompt=args.prompt,
            skill=args.skill,
            code=args.code,
            codes=args.codes,
            date=args.date,
            start_date=args.start_date,
            end_date=args.end_date,
            profile=args.profile,
            toolsets=args.toolsets,
            model=args.model,
            timeout_sec=args.timeout_sec,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
