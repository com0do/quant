from __future__ import annotations

from datetime import datetime
import time
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from quant.config import load_config
from quant.execution.algo import twap_slices, vwap_slices
from quant.execution.factory import build_broker
from quant.execution.models import Order, OrderRequest, OrderSide, OrderStatus
from quant.execution.risk_guard import RiskGuard, RiskPolicy
from quant.live.archive import ArchiveWriter
from quant.live.daily_report import generate_daily_report
from quant.live.execution_plan import load_execution_plan
from quant.live.intraday_monitor import intraday_sell_scores
from quant.live.scanner import run_preopen_scan
from quant.live.signal_engine import generate_live_scores
from quant.stock_data.data_service import DataService


@dataclass
class ScheduledOrder:
    run_at_ts: float
    req: OrderRequest


@dataclass
class PendingOrder:
    order: Order
    submit_ts: float
    chase_count: int
    chase_mode: Literal["passive", "aggressive"]
    anchor_price: float


def _parse_sessions(sessions: list[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s in sessions:
        try:
            a, b = s.split("-")
            ah, am = [int(x) for x in a.split(":")]
            bh, bm = [int(x) for x in b.split(":")]
            out.append((ah * 60 + am, bh * 60 + bm))
        except Exception:
            continue
    return out


def _in_session(now: pd.Timestamp, sessions_min: list[tuple[int, int]]) -> bool:
    minute = now.hour * 60 + now.minute
    return any(a <= minute <= b for a, b in sessions_min)


def _latest_close(data: DataService, code: str, asof: pd.Timestamp) -> float | None:
    # Prefer gateway real-time quote when configured; fallback to daily close from DB/JQ.
    rt = data.get_realtime_last_price(code)
    if rt is not None and rt > 0:
        return float(rt)
    panel = data.get_price_panel([code], asof.strftime("%Y-%m-%d"), asof.strftime("%Y-%m-%d"))
    if panel.empty:
        return None
    try:
        return float(panel.sort_values("date").iloc[-1]["close"])
    except Exception:
        return None


def _schedule_slices(req: OrderRequest, cfg) -> list[ScheduledOrder]:
    if str(req.algo).lower() == "vwap":
        plans = vwap_slices(req.quantity, cfg.daemon.vwap_profile, cfg.daemon.slice_interval_sec)
    else:
        plans = twap_slices(req.quantity, cfg.daemon.twap_slices, cfg.daemon.slice_interval_sec)
    now_ts = time.time()
    return [ScheduledOrder(run_at_ts=now_ts + p.offset_sec, req=OrderRequest(code=req.code, side=req.side, quantity=p.quantity, price=req.price, algo=req.algo)) for p in plans]


def _chase_price(price: float, side: OrderSide, bps: float, mode: str) -> float:
    step = max(0.0, bps) / 10000.0
    if mode == "passive":
        mult = 1.0 + (step if side == OrderSide.BUY else -step)
    else:  # aggressive
        mult = 1.0 + (2 * step if side == OrderSide.BUY else -2 * step)
    return max(0.0001, price * mult)


def _quote_float(quote: dict, *keys: str) -> float | None:
    for k in keys:
        v = quote.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def _is_price_deviation_ok(req: OrderRequest, quote: dict, cfg) -> bool:
    if not bool(getattr(cfg.daemon, "anti_chase_enable", True)):
        return True
    px = float(req.price or 0.0)
    if px <= 0:
        return True
    last = _quote_float(quote, "lastPrice", "last", "last_price", "price", "close")
    if last is None or last <= 0:
        return True
    if req.side == OrderSide.BUY:
        max_bps = max(0.0, float(getattr(cfg.daemon, "max_buy_deviation_bps", 20.0)))
        return px <= last * (1.0 + max_bps / 10000.0)
    max_bps = max(0.0, float(getattr(cfg.daemon, "max_sell_deviation_bps", 30.0)))
    return px >= last * (1.0 - max_bps / 10000.0)


def _is_anti_fomo_blocked(code: str, quote: dict, cfg) -> bool:
    if not bool(getattr(cfg.daemon, "anti_fomo_enable", True)):
        return False
    last = _quote_float(quote, "lastPrice", "last", "last_price", "price", "close")
    high = _quote_float(quote, "high", "highPrice")
    low = _quote_float(quote, "low", "lowPrice")
    pre_close = _quote_float(quote, "lastClose", "preClose", "pre_close", "prev_close")
    if last is None or high is None or low is None or pre_close is None:
        return False
    if last <= 0 or high <= 0 or low <= 0 or pre_close <= 0:
        return False
    intraday_ret = last / pre_close - 1.0
    day_range = max(1e-9, high - low)
    upper_shadow_ratio = max(0.0, (high - last) / day_range)
    near_high_ratio = last / high
    ret_thr = float(getattr(cfg.daemon, "anti_fomo_intraday_ret_threshold", 0.06))
    shadow_thr = float(getattr(cfg.daemon, "anti_fomo_upper_shadow_threshold", 0.45))
    near_high_thr = float(getattr(cfg.daemon, "anti_fomo_near_high_threshold", 0.985))
    # Intraday spike + long upper shadow + fallback from high => likely climax trap, block chasing buy.
    return bool(
        intraday_ret >= ret_thr
        and upper_shadow_ratio >= shadow_thr
        and near_high_ratio <= near_high_thr
    )


def run_daemon(config_path: str | None = None, max_loops: int | None = None) -> None:
    cfg = load_config(config_path=config_path)
    data = DataService(cfg)
    broker = build_broker(cfg)
    risk = RiskGuard(
        RiskPolicy(
            max_single_position_pct=float(cfg.risk.max_single_position_pct),
            reserve_cash_pct=float(cfg.risk.reserve_cash_pct),
            max_daily_orders=int(cfg.risk.max_daily_orders),
            lot_size=int(getattr(cfg.risk, "lot_size", 100)),
            max_daily_notional_pct=float(getattr(cfg.risk, "max_daily_notional_pct", 1.2)),
        )
    )
    archive = ArchiveWriter()

    sessions_min = _parse_sessions(cfg.daemon.market_sessions)
    loops = 0
    watchlist: list[str] = []
    execution_plan = load_execution_plan(str(getattr(cfg.daemon, "execution_plan_path", "")))
    if bool(getattr(cfg.daemon, "preopen_use_execution_plan_watchlist", True)):
        wl = execution_plan.get("watchlist", []) if isinstance(execution_plan, dict) else []
        if isinstance(wl, list):
            watchlist = [str(x) for x in wl if str(x)]
    slice_queue: list[ScheduledOrder] = []
    pending: dict[str, PendingOrder] = {}
    errors = 0
    day_start_equity = float(broker.get_account().get("equity", 1_000_000.0))
    intraday_peak = day_start_equity
    had_session = False

    while True:
        now = pd.Timestamp.now()
        try:
            in_session = _in_session(now, sessions_min)
            if not in_session:
                if cfg.daemon.preopen_scan_enabled and not watchlist:
                    watchlist = run_preopen_scan(data, cfg, now)[: cfg.daemon.preopen_scan_top_k]
                    archive.record({"ts": now.isoformat(), "event": "preopen_scan", "watchlist": len(watchlist)})
                if had_session and cfg.daemon.exit_after_close:
                    archive.record({"ts": now.isoformat(), "event": "close_exit"})
                    break
                loops += 1
                if max_loops is not None and loops >= max_loops:
                    break
                time.sleep(max(1, cfg.daemon.poll_interval_sec))
                continue

            had_session = True
            positions = broker.get_positions()
            held = list(positions.keys())
            universe = list(dict.fromkeys(watchlist + held))
            buy_scores, sell_scores = generate_live_scores(data, cfg, asof=now, universe=universe, held_codes=held)
            intraday = intraday_sell_scores(data, cfg, asof=now, held_codes=held)
            if not intraday.empty:
                sell_scores = sell_scores.combine(intraday, max, fill_value=0.0)

            # Build target intents (sell first).
            intents: list[OrderRequest] = []
            for code in held:
                urgency = float(sell_scores.get(code, 0.0))
                if urgency < cfg.strategy.sell_score_threshold:
                    continue
                px = _latest_close(data, code, now)
                if px is None:
                    continue
                intents.append(
                    OrderRequest(
                        code=code,
                        side=OrderSide.SELL,
                        quantity=int(positions.get(code, 0)),
                        price=px,
                        algo=cfg.daemon.sell_algo,
                    )
                )
            slots = max(0, cfg.strategy.max_positions - len(positions))
            for code in [c for c in buy_scores.index if c not in held][:slots]:
                score = float(buy_scores.get(code, 0.0))
                if score < cfg.strategy.buy_score_threshold:
                    continue
                px = _latest_close(data, code, now)
                if px is None:
                    continue
                intents.append(
                    OrderRequest(
                        code=code,
                        side=OrderSide.BUY,
                        quantity=100,
                        price=px,
                        algo=cfg.daemon.buy_algo,
                    )
                )

            # Schedule slices, avoid duplicates by side+code when already pending.
            pending_key = {f"{v.order.req.code}:{v.order.req.side.value}" for v in pending.values()}
            for it in intents:
                key = f"{it.code}:{it.side.value}"
                if key in pending_key:
                    continue
                slice_queue.extend(_schedule_slices(it, cfg))

            # Fire due slices.
            due = [x for x in slice_queue if x.run_at_ts <= time.time()]
            slice_queue = [x for x in slice_queue if x.run_at_ts > time.time()]
            if due:
                submitted = 0
                rejected = 0
                for d in due:
                    allow = risk.allow_order(d.req, broker.get_account(), broker.get_positions())
                    if not allow:
                        rejected += 1
                        continue
                    quote = data.get_realtime_quote(d.req.code)
                    if not _is_price_deviation_ok(d.req, quote, cfg):
                        rejected += 1
                        archive.record(
                            {
                                "ts": now.isoformat(),
                                "event": "reject_price_deviation",
                                "code": d.req.code,
                                "side": d.req.side.value,
                                "price": float(d.req.price or 0.0),
                            }
                        )
                        continue
                    if d.req.side == OrderSide.BUY and _is_anti_fomo_blocked(d.req.code, quote, cfg):
                        rejected += 1
                        archive.record(
                            {
                                "ts": now.isoformat(),
                                "event": "reject_anti_fomo",
                                "code": d.req.code,
                                "price": float(d.req.price or 0.0),
                            }
                        )
                        continue
                    order = broker.submit_order(d.req) if not cfg.daemon.dry_run else Order(order_id=f"dry-{time.time_ns()}", req=d.req, status=OrderStatus.NEW)
                    mode = cfg.daemon.buy_chase_mode if d.req.side == OrderSide.BUY else cfg.daemon.sell_chase_mode
                    pending[order.order_id] = PendingOrder(
                        order=order,
                        submit_ts=time.time(),
                        chase_count=0,
                        chase_mode=mode,
                        anchor_price=float(d.req.price or 0.0),
                    )  # tracked for timeout logic
                    submitted += 1
                archive.record(
                    {
                        "ts": now.isoformat(),
                        "event": "dispatch_due",
                        "due": len(due),
                        "submitted": submitted,
                        "rejected": rejected,
                        "queue_left": len(slice_queue),
                    }
                )

            # Timeout + chase logic.
            for oid, p in list(pending.items()):
                od = broker.get_order(oid) if not cfg.daemon.dry_run else p.order
                if od is None:
                    pending.pop(oid, None)
                    continue
                if od.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
                    pending.pop(oid, None)
                    continue
                if time.time() - p.submit_ts < max(1, cfg.daemon.order_timeout_sec):
                    continue
                broker.cancel_order(oid)
                if p.chase_count >= cfg.daemon.max_chase_count:
                    pending.pop(oid, None)
                    archive.record({"ts": now.isoformat(), "event": "timeout_drop", "order_id": oid})
                    continue
                base_px = float(p.order.req.price or 0.0)
                new_px = _chase_price(base_px, p.order.req.side, cfg.daemon.chase_step_bps, p.chase_mode)
                if bool(getattr(cfg.daemon, "anti_chase_enable", True)) and p.anchor_price > 0:
                    if p.order.req.side == OrderSide.BUY:
                        cum_bps = (new_px / p.anchor_price - 1.0) * 10000.0
                    else:
                        cum_bps = (1.0 - new_px / p.anchor_price) * 10000.0
                    if cum_bps > float(getattr(cfg.daemon, "max_total_chase_bps", 24.0)):
                        pending.pop(oid, None)
                        archive.record(
                            {
                                "ts": now.isoformat(),
                                "event": "chase_drop_total_bps",
                                "order_id": oid,
                                "cum_bps": float(cum_bps),
                            }
                        )
                        continue
                new_req = OrderRequest(
                    code=p.order.req.code,
                    side=p.order.req.side,
                    quantity=max(1, p.order.req.quantity - p.order.filled_qty),
                    price=new_px,
                    algo=p.order.req.algo,
                )
                quote = data.get_realtime_quote(new_req.code)
                if not _is_price_deviation_ok(new_req, quote, cfg):
                    pending.pop(oid, None)
                    archive.record(
                        {
                            "ts": now.isoformat(),
                            "event": "chase_drop_price_deviation",
                            "order_id": oid,
                            "price": float(new_px),
                        }
                    )
                    continue
                if new_req.side == OrderSide.BUY and _is_anti_fomo_blocked(new_req.code, quote, cfg):
                    pending.pop(oid, None)
                    archive.record(
                        {
                            "ts": now.isoformat(),
                            "event": "chase_drop_anti_fomo",
                            "order_id": oid,
                            "code": new_req.code,
                        }
                    )
                    continue
                if not cfg.daemon.dry_run:
                    new_order = broker.submit_order(new_req)
                else:
                    new_order = Order(order_id=f"dry-chase-{time.time_ns()}", req=new_req, status=OrderStatus.NEW)
                pending.pop(oid, None)
                pending[new_order.order_id] = PendingOrder(
                    order=new_order,
                    submit_ts=time.time(),
                    chase_count=p.chase_count + 1,
                    chase_mode=p.chase_mode,
                    anchor_price=p.anchor_price,
                )
                archive.record({"ts": now.isoformat(), "event": "timeout_chase", "from": oid, "to": new_order.order_id, "chase_count": p.chase_count + 1})

            # Fuse checks.
            acct = broker.get_account()
            equity = float(acct.get("equity", acct.get("cash", 0.0)))
            intraday_peak = max(intraday_peak, equity)
            daily_loss = equity / max(day_start_equity, 1e-9) - 1.0
            intraday_dd = equity / max(intraday_peak, 1e-9) - 1.0
            if daily_loss <= -cfg.daemon.max_daily_loss_pct or intraday_dd <= -cfg.daemon.max_intraday_drawdown_pct:
                archive.record(
                    {
                        "ts": now.isoformat(),
                        "event": "fuse_triggered",
                        "daily_loss": daily_loss,
                        "intraday_dd": intraday_dd,
                    }
                )
                break

            loops += 1
            if max_loops is not None and loops >= max_loops:
                break
            time.sleep(max(1, cfg.daemon.poll_interval_sec))

        except Exception as exc:
            errors += 1
            archive.record({"ts": now.isoformat(), "event": "daemon_error", "error": str(exc)[:300], "errors": errors})
            if errors >= cfg.daemon.max_consecutive_errors:
                archive.record({"ts": now.isoformat(), "event": "error_fuse", "errors": errors})
                break
            time.sleep(max(1, cfg.daemon.poll_interval_sec))

    day = datetime.now().strftime("%Y-%m-%d")
    events_file = archive.flush(day=day)
    generate_daily_report(day=day, account=broker.get_account(), positions=broker.get_positions(), events_file=events_file)
