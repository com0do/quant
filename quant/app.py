from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime

from quant.backtest.engine import run_backtest
from quant.config import load_config
from quant.live.daemon import run_daemon
from quant.live.daily_report import generate_daily_report
from quant.live.execution_plan import ExecutionPlan, save_execution_plan
from quant.live.scanner import run_preopen_scan
from quant.stock_data.data_service import DataService
from quant.tools.cleanup_pickle import run_cleanup_pickle
from quant.tools.factor_analyze import run_factor_analyze
from quant.tools.jq_bulk_sync import run_jq_bulk_sync
from quant.tools.mode_consistency import run_mode_consistency
from quant.tools.optimize import run_optimize, run_walkforward_gate
from quant.tools.pickle_to_db import run_pickle_to_db
from quant.tools.prefetch import run_prefetch
from quant.tools.profile_scan import run_profile_scan
from quant.tools.scan_optimize import run_scan_optimize
from quant.tools.solidify_params import run_solidify_params
from quant.tools.sync_plan import run_sync_plan
from quant.tools.vectorbt_optimize import run_vectorbt_optimize
from quant.tools.walk_forward import run_walk_forward


def _build_live_execution_plan(config_path: str | None = None, sync_before_open: bool = True) -> dict:
    cfg = load_config(config_path=config_path)
    if sync_before_open:
        run_jq_bulk_sync(config_path=config_path)
    bt = run_backtest(cfg, output_prefix="preopen_backtest")
    asof = datetime.now()
    data = DataService(cfg)
    watchlist = run_preopen_scan(data, cfg, asof=asof)[: int(cfg.daemon.preopen_scan_top_k)]
    plan = ExecutionPlan(
        plan_date=asof.strftime("%Y-%m-%d"),
        generated_at=asof.isoformat(timespec="seconds"),
        source_config_path=str(config_path or ""),
        benchmark_index=str(cfg.data.benchmark_index),
        watchlist=[str(x) for x in watchlist],
        backtest_metrics=dict(bt.metrics),
        strategy_snapshot={
            "strategy_names": list(cfg.strategy.strategy_names),
            "strategy_weights": [float(x) for x in cfg.strategy.strategy_weights],
            "buy_score_threshold": float(cfg.strategy.buy_score_threshold),
            "sell_score_threshold": float(cfg.strategy.sell_score_threshold),
            "top_k": int(cfg.strategy.top_k),
            "max_positions": int(cfg.strategy.max_positions),
        },
    )
    plan_path = save_execution_plan(str(cfg.daemon.execution_plan_path), plan)
    return {"plan_path": plan_path, "watchlist_size": len(watchlist), "metrics": dict(bt.metrics)}


def run_backtest_app(config_path: str | None = None) -> None:
    cfg = load_config(config_path=config_path)
    res = run_backtest(cfg, output_prefix="backtest")
    Path("output/backtest_metrics.json").write_text(json.dumps(res.metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def run_jq_bulk_sync_app(config_path: str | None = None) -> None:
    run_jq_bulk_sync(config_path=config_path)


def run_scan_optimize_app(config_path: str | None = None) -> None:
    cfg = load_config(config_path=config_path)
    run_scan_optimize(workers=cfg.daemon.preopen_scan_threads, config_path=config_path)


def run_iterative_optimize_app(config_path: str | None = None) -> None:
    from quant.tools.iterative_optimize import run_iterative_optimize

    run_iterative_optimize(config_path=config_path)


def run_live_daemon_app(config_path: str | None = None) -> None:
    run_daemon(config_path=config_path, max_loops=1)


def run_live_auto_app(config_path: str | None = None) -> None:
    """
    Single-source live pipeline:
    1) Optional pre-open data sync
    2) Pre-open backtest on current config
    3) Build today's execution plan (watchlist + metrics + strategy snapshot)
    4) Start live daemon consuming the execution plan
    """
    sync_before_open = str(os.getenv("LIVE_AUTO_SYNC_BEFORE_OPEN", "1")).strip().lower() not in {"0", "false", "no"}
    _build_live_execution_plan(config_path=config_path, sync_before_open=sync_before_open)
    loops_env = os.getenv("LIVE_AUTO_MAX_LOOPS")
    max_loops = int(loops_env) if (loops_env is not None and str(loops_env).strip()) else None
    run_daemon(config_path=config_path, max_loops=max_loops)


def run_prepare_live_plan_app(config_path: str | None = None) -> None:
    """
    Pre-open preparation only:
    - optional sync
    - backtest
    - pre-open scan
    - write today's execution plan
    """
    sync_before_open = str(os.getenv("LIVE_AUTO_SYNC_BEFORE_OPEN", "1")).strip().lower() not in {"0", "false", "no"}
    out = _build_live_execution_plan(config_path=config_path, sync_before_open=sync_before_open)
    Path("output").mkdir(exist_ok=True)
    Path("output/prepare_live_plan_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_daily_report_app() -> None:
    generate_daily_report(day=Path(".").resolve().name, account={"cash": 0, "equity": 0}, positions={})


def run_prefetch_app(config_path: str | None = None) -> None:
    run_prefetch(config_path=config_path)


def run_optimize_app(config_path: str | None = None) -> None:
    run_optimize(config_path=config_path)


def run_optimize_fast_app(config_path: str | None = None) -> None:
    """
    One-shot acceleration pipeline:
    1) vectorbt quick pre-screen
    2) process-based fine optimize
    3) write compact summary artifact
    """
    vb = run_vectorbt_optimize(config_path=config_path)
    base_cfg = load_config(config_path=config_path)
    df = run_optimize(config_path=config_path, run_vectorbt_prefetch=False)
    wf_top_n = max(1, int(os.getenv("OPT_WF_TOPN", "6")))
    wf = run_walkforward_gate(df, base_cfg=base_cfg, top_n=wf_top_n)
    best = wf.iloc[0].to_dict() if not wf.empty else (df.iloc[0].to_dict() if not df.empty else {})
    summary = {
        "status": "ok" if best else "empty",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vectorbt_rows": int(len(vb)),
        "opt_rows": int(len(df)),
        "walkforward_rows": int(len(wf)),
        "best": best,
    }
    if not wf.empty:
        wf.to_csv("output/optimize_fast_walkforward.csv", index=False)
    Path("output/optimize_fast_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_profile_scan_app() -> None:
    run_profile_scan()


def run_pickle_to_db_app() -> None:
    run_pickle_to_db()


def run_cleanup_pickle_app() -> None:
    run_cleanup_pickle()


def run_factor_analyze_app(config_path: str | None = None) -> None:
    cfg = load_config(config_path=config_path)
    run_factor_analyze(db_path=cfg.data.sqlite_db_path)


def run_vectorbt_optimize_app(config_path: str | None = None) -> None:
    run_vectorbt_optimize(config_path=config_path)


def run_solidify_params_app() -> None:
    run_solidify_params()


def run_sync_plan_app(config_path: str | None = None) -> None:
    run_sync_plan(config_path=config_path)


def run_mode_consistency_app(config_path: str | None = None) -> None:
    run_mode_consistency(config_path=config_path)


def run_walk_forward_app(config_path: str | None = None) -> None:
    """Walk-forward backtest with rolling train/validation windows."""
    run_walk_forward(config_path=config_path or "config/final_freeze_2025.toml")


def run_gbm_train_app(
    config_path: str | None = None,
    db_path: str | None = None,
    start_date: str = "2023-01-01",
    end_date: str = "2025-06-30",
    forward_days: int = 5,
    model_path: str = "data/models/gbm_multifactor.txt",
) -> None:
    """Train the GBM multifactor aggregator (LightGBM)."""
    from quant.ml.gbm.factor_gbm import run_train

    metrics = run_train(
        db_path=db_path,
        start_date=start_date,
        end_date=end_date,
        forward_days=forward_days,
        model_path=model_path,
        config_path=config_path,
    )
    Path("output").mkdir(exist_ok=True)
    Path("output/gbm_train_summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_nlp_analyze_app(
    input_glob: str = "data/nlp/inbox/*.jsonl",
    db_path: str = "data/nlp/signals.db",
    limit: int = 0,
    dry_run: bool = False,
) -> None:
    """Run the LLM (DeepSeek) announcement pipeline: JSONL → SQLite structured signals.

    This is a **single-shot structured extraction**. For multi-step agent workflows
    (查行情 + 查公告 + 判断) see `--mode hermes-chat`.
    """
    from quant.ml.llm.announcement_pipeline import run_batch

    stats = run_batch(input_glob=input_glob, db_path=db_path, limit=limit, dry_run=dry_run)
    Path("output").mkdir(exist_ok=True)
    Path("output/nlp_analyze_summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def run_hermes_chat_app(
    prompt: str | None = None,
    skill: str | None = None,
    code: str | None = None,
    codes: str | None = None,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    profile: str = "quant",
    toolsets: str = "core,quant",
    model: str | None = None,
    timeout_sec: int = 600,
) -> None:
    """Talk to hermes-agent via `hermes -z` CLI, scoped to a profile.

    Two usage modes:
    1. Free-form:   --prompt "..."   (skill 参数留空)
    2. Skill-first: --skill analyst_zoe --code 000001.XSHE --date 2025-08-01

    Profile 隔离：默认走 profile "quant"（`bash hermes_plugin/setup.sh` 创建），
    不动你已有的默认 profile。可通过 `--profile` 换目标 profile。
    """
    from quant.ml.hermes import runner

    if skill:
        extra_prompt = prompt.strip() if prompt else None
        if codes and not code:
            extra_prompt = ((extra_prompt + "\n") if extra_prompt else "") + f"股票池：{codes}"
        result = runner.run_skill(
            skill=skill,
            code=code,
            date=date,
            start_date=start_date,
            end_date=end_date,
            extra_prompt=extra_prompt,
            profile=profile,
            timeout_sec=timeout_sec,
        )
    else:
        if not prompt:
            print(json.dumps({"error": "one of --prompt or --skill is required"}, ensure_ascii=False))
            return
        extra = {"code": code, "codes": codes, "date": date,
                 "start_date": start_date, "end_date": end_date}
        extra = {k: v for k, v in extra.items() if v}
        result = runner.chat(
            prompt,
            profile=profile,
            toolsets=toolsets,
            model=model,
            timeout_sec=timeout_sec,
            extra=extra or None,
        )

    Path("output").mkdir(exist_ok=True)
    Path("output/hermes_chat_last.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if "response" in result:
        print(result["response"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
