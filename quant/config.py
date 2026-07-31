from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


@dataclass
class DataConfig:
    # Data source and local persistence.
    source: str = "sqlite"
    sqlite_db_path: str = "data/market_cache.db"
    runtime_csi300_500_db_path: str = "data/runtime/market_csi300_500_latest.db"
    runtime_csi1000_db_path: str = "data/runtime/market_csi1000_latest.db"
    runtime_csi2000_db_path: str = "data/runtime/market_csi2000_latest.db"
    meta_constituents_db_path: str = "data/meta/index_constituents.db"
    enable_index_aware_db: bool = False
    cross_index_hold_enable: bool = True
    cross_index_candidates: list[str] = field(default_factory=lambda: ["000905.XSHG", "000300.XSHG", "399303.XSHE"])
    runtime_window_days: int = 548
    archive_overflow_days: int = 61
    archive_move_chunk_days: int = 61

    # Backtest or sync date range.
    start_date: str = "2024-12-23"
    end_date: str = "2025-12-23"

    # Universe and benchmark settings.
    benchmark_index: str = "000852.XSHG"
    universe_mode: str = "benchmark_members"
    prefetch_universe_limit: int = 0

    # JQ synchronization budget and chunk controls.
    jq_sync_daily_row_budget: int = 0
    jq_sync_max_calls: int = 0
    jq_sync_chunk_days: int = 120

    # Fallback source for near-realtime quote reads.
    realtime_quote_source: str = "daily_close"


@dataclass
class StrategyConfig:
    # Multi-strategy blend and portfolio construction.
    strategy_names: list[str] = field(default_factory=lambda: ["price_only_cross_section", "csi1000_enhanced"])
    strategy_weights: list[float] = field(default_factory=lambda: [0.6, 0.4])
    buy_score_threshold: float = 0.55
    sell_score_threshold: float = 0.62
    entry_interval_days: int = 6
    top_k: int = 15
    max_positions: int = 15

    # Position protection and minimum holding constraints.
    stop_loss_pct: float = 0.03
    trailing_stop_pct: float = 0.08
    min_hold_days: int = 2
    # Laddered moving stop (A/B/C/D/E style):
    # when unrealized return reaches each level, tighten trailing stop.
    dynamic_trailing_stop_enable: bool = False
    dynamic_trailing_profit_levels: list[float] = field(default_factory=lambda: [0.08, 0.15, 0.25, 0.35])
    dynamic_trailing_stop_levels: list[float] = field(default_factory=lambda: [0.10, 0.08, 0.06, 0.05])
    # Structured sell-point rules (A/B/C style).
    sell_point_rule_enable: bool = True
    sell_point_top_break_enable: bool = True
    sell_point_box_break_enable: bool = True
    sell_point_ma_break_enable: bool = True
    sell_point_ma_break_confirm_days: int = 2
    sell_point_take_profit_enable: bool = True
    sell_point_take_profit_pct: float = 0.35

    # Weekly adaptive allocation bounds.
    weekly_auto_weight: bool = False
    weekly_adaptive_min: float = 0.2
    weekly_adaptive_max: float = 0.8
    weekly_adaptive_neutral: float = 0.5

    # Dual-layer weighting: defensive sleeve (adaptive_regime_midterm) vs offensive sleeve (csi1000_enhanced)
    dual_layer_enable: bool = True
    dual_layer_defense_floor: float = 0.25
    dual_layer_defense_ceiling: float = 0.65
    dual_layer_offense_floor: float = 0.35
    dual_layer_offense_ceiling: float = 0.75
    dual_layer_rel_lookback_days: int = 20
    dual_layer_rel_sensitivity: float = 1.0
    dual_layer_breadth_center: float = 0.56
    dual_layer_breadth_width: float = 0.20

    # Value and trend filters before candidate admission.
    value_filter_strict: bool = True
    require_above_ma200: bool = True
    require_above_ma120: bool = False
    ma_trend_filter_buffer_bps: float = 20.0
    ma_trend_confirm_days: int = 2

    # Anti-chase filters and controlled MA200 exception.
    anti_fomo_filter_enable: bool = True
    anti_fomo_ret5_threshold: float = 0.08
    anti_fomo_vol_ratio_threshold: float = 1.8
    anti_fomo_upper_shadow_threshold: float = 0.35
    allow_short_below_ma200_exception: bool = True
    ma200_exception_min_ret5: float = 0.04
    ma200_exception_vol_ratio_min: float = 1.2
    ma200_exception_vol_ratio_max: float = 2.4

    # Volatility gates and risk budget caps.
    open_vol_min: float = 0.008
    open_vol_max: float = 0.045
    max_single_trade_risk_pct: float = 0.01
    max_single_position_loss_pct: float = 0.05
    max_total_exposure_pct: float = 0.50

    # Signal-strength-based sizing adjustments.
    signal_weighted_allocation_enable: bool = False
    signal_weighted_power: float = 1.5
    signal_weighted_max_ratio: float = 0.55

    # Regime switch deltas for key strategy knobs.
    regime_param_switch_enable: bool = False
    regime_switch_breadth_risk_on: float = 0.62
    regime_switch_breadth_risk_off: float = 0.45
    regime_switch_top_k_delta_on: int = 0
    regime_switch_top_k_delta_off: int = 0
    regime_switch_sell_thr_delta_on: float = 0.0
    regime_switch_sell_thr_delta_off: float = 0.0
    regime_switch_entry_interval_mult_on: float = 1.0
    regime_switch_entry_interval_mult_off: float = 1.0
    regime_switch_exposure_mult_on: float = 1.0
    regime_switch_exposure_mult_off: float = 1.0

    # Monthly dynamic exposure and crash cooldown controls.
    monthly_dynamic_exposure_enable: bool = False
    monthly_exposure_min_pct: float = 0.40
    monthly_exposure_max_pct: float = 1.00
    crash_ret5_threshold: float = -0.06
    crash_ret20_threshold: float = -0.12
    crash_breadth_threshold: float = 0.35
    crash_cooldown_days: int = 3

    # Market gate and temporary pause after losses.
    market_regime_gate_enable: bool = True
    market_regime_min_win_prob: float = 0.60
    market_regime_min_breadth: float = 0.50
    pause_after_consecutive_loss_days: int = 2
    pause_after_loss_pause_days: int = 1
    trend_extend_hold: bool = True

    # CSI1000 model feature weights.
    csi_value_weight: float = 0.7716
    csi_quality_weight: float = 0.8284
    csi_mom20_weight: float = 0.55
    csi_mom60_weight: float = 0.25
    csi_vol_weight: float = 0.30


@dataclass
class DaemonConfig:
    # Pre-open scanning and polling cycle.
    preopen_scan_threads: int = 4
    preopen_scan_enabled: bool = True
    preopen_scan_top_k: int = 30
    preopen_scan_max_universe: int = 0
    poll_interval_sec: int = 5
    market_sessions: list[str] = field(default_factory=lambda: ["09:30-11:30", "13:00-15:00"])

    # Execution algorithm and slicing schedule.
    buy_algo: str = "twap"
    sell_algo: str = "twap"
    twap_slices: int = 4
    vwap_profile: list[float] = field(default_factory=lambda: [0.2, 0.3, 0.3, 0.2])
    slice_interval_sec: int = 60
    order_timeout_sec: int = 300

    # Price-chase behavior during order management.
    buy_chase_mode: str = "passive"
    sell_chase_mode: str = "aggressive"
    chase_step_bps: float = 8.0
    max_chase_count: int = 2

    # Runtime stability guardrails.
    max_consecutive_errors: int = 10
    max_daily_loss_pct: float = 0.03
    max_intraday_drawdown_pct: float = 0.03
    exit_after_close: bool = True
    dry_run: bool = False
    broker_type: str = "paper"

    # Exposure and liquidity constraints.
    exposure_file: str = "data/stock_exposure.csv"
    max_industry_exposure_pct: float = 0.35
    max_style_exposure_pct: float = 0.40
    liquidity_lookback_days: int = 20
    min_adv_notional: float = 5_000_000.0
    max_adv_notional_ratio_per_slice: float = 0.05
    max_adv_volume_ratio_per_slice: float = 0.05
    max_daily_notional_per_stock: float = 2_000_000.0

    # Plan file and anti-chase / anti-fomo intraday gates.
    execution_plan_path: str = "data/runtime/live_execution_plan.json"
    preopen_use_execution_plan_watchlist: bool = True
    anti_chase_enable: bool = True
    max_buy_deviation_bps: float = 20.0
    max_sell_deviation_bps: float = 30.0
    max_total_chase_bps: float = 24.0
    anti_fomo_enable: bool = True
    anti_fomo_intraday_ret_threshold: float = 0.06
    anti_fomo_upper_shadow_threshold: float = 0.45
    anti_fomo_near_high_threshold: float = 0.985


@dataclass
class RiskConfig:
    # Account-level risk and order limits.
    max_single_position_pct: float = 0.2
    reserve_cash_pct: float = 0.05
    max_daily_orders: int = 100
    lot_size: int = 100
    max_daily_notional_pct: float = 1.2
    max_daily_loss_pct: float = 0.05
    max_intraday_loss_pct: float = 0.03
    max_portfolio_drawdown_pct: float = 0.2


@dataclass
class GatewayConfig:
    # Unified external broker gateway settings.
    base_url: str = "http://127.0.0.1:18080"
    token: str = ""
    timeout_sec: int = 8
    account_id: str = ""


@dataclass
class ExecutionConfig:
    # Backtest execution assumptions.
    initial_cash: float = 100000.0
    buy_fee: float = 0.0003
    sell_fee: float = 0.0013
    slippage_bps: float = 5.0


@dataclass
class AppConfig:
    # Root config aggregating all sub-config sections.
    data: DataConfig = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


def resolve_gateway_settings(cfg: AppConfig) -> GatewayConfig:
    base_url = str(getattr(cfg.gateway, "base_url", "")).strip()
    token = str(getattr(cfg.gateway, "token", ""))
    timeout_sec = int(getattr(cfg.gateway, "timeout_sec", 8) or 8)
    account_id = str(getattr(cfg.gateway, "account_id", ""))
    return GatewayConfig(
        base_url=base_url,
        token=token,
        timeout_sec=max(1, timeout_sec),
        account_id=account_id,
    )


def _merge_dataclass(dc: Any, updates: dict[str, Any]) -> None:
    for k, v in updates.items():
        if not hasattr(dc, k):
            continue
        cur = getattr(dc, k)
        if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
            _merge_dataclass(cur, v)
        else:
            setattr(dc, k, v)


def load_config(config_path: str | None = None) -> AppConfig:
    cfg = AppConfig()
    candidate_files: list[Path] = []
    if config_path:
        candidate_files.append(Path(config_path))
    for p in candidate_files:
        if not p.exists():
            continue
        with p.open("rb") as f:
            data = tomllib.load(f)
        if isinstance(data, dict):
            _merge_dataclass(cfg, data)
    return cfg


