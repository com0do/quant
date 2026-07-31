#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import redis

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.bridge.jq_signal import (
    DEFAULT_QUEUE_KEY,
    DEFAULT_STALE_AFTER_SEC,
    JqSignal,
    lot_target_quantity,
    quote_last_price,
    round_price_for_code,
    signal_is_stale,
    verify_signal_signature,
)
from quant.config import load_config, resolve_gateway_settings
from quant.gateway.client import GatewayClient, normalize_code


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Consume JoinQuant signals from Redis and submit to xtquant gateway.")
    p.add_argument("--config", default="config/live_qmt_http.toml", help="App config path for gateway settings.")
    p.add_argument("--redis-url", default="", help="Redis URL, e.g. redis://:password@host:6379/0")
    p.add_argument("--queue-key", default=DEFAULT_QUEUE_KEY, help="Redis list key storing signal JSON messages.")
    p.add_argument("--poll-timeout", type=int, default=5, help="BRPOPLPUSH timeout seconds.")
    p.add_argument("--stale-after-sec", type=int, default=DEFAULT_STALE_AFTER_SEC, help="Discard signals older than this.")
    p.add_argument("--idempotency-ttl-sec", type=int, default=3 * 24 * 3600, help="TTL for processed client_order_id keys.")
    p.add_argument("--inflight-ttl-sec", type=int, default=600, help="TTL for in-flight dedup lock.")
    p.add_argument("--max-retries", type=int, default=3, help="Retry attempts before dead-letter.")
    p.add_argument("--hmac-secret", default=os.getenv("JQ_SIGNAL_HMAC_SECRET", ""), help="Optional HMAC secret for verifying payload signature.")
    p.add_argument("--lot-size", type=int, default=100, help="Round quantity to this lot size.")
    p.add_argument("--dry-run", action="store_true", help="Print order plans without submitting.")
    p.add_argument("--once", action="store_true", help="Process at most one message then exit.")
    return p.parse_args()


def _dead_letter(raw: str, queue_key: str, r: redis.Redis, error: str) -> None:
    payload = {"error": str(error), "raw": raw, "failed_at": int(time.time())}
    r.lpush(f"{queue_key}:dead", json.dumps(payload, ensure_ascii=False))


def _retry_later(raw: str, queue_key: str, r: redis.Redis) -> None:
    r.rpush(queue_key, raw)


def _signal_target_value(signal: JqSignal, account: dict[str, Any]) -> float:
    if signal.target_value is not None:
        return max(0.0, float(signal.target_value))
    if signal.target_pct is not None:
        equity = float(account.get("equity") or account.get("total_asset") or 0.0)
        return max(0.0, equity * float(signal.target_pct))
    return 0.0


def _build_order_plan(
    signal: JqSignal,
    client: GatewayClient,
    lot_size: int,
) -> dict[str, Any] | None:
    account = client.get_account()
    positions_raw = client.get_positions_raw()
    positions: dict[str, int] = {}
    if isinstance(positions_raw, dict):
        positions = {normalize_code(k): int(v or 0) for k, v in positions_raw.items()}
    elif isinstance(positions_raw, list):
        for row in positions_raw:
            if not isinstance(row, dict):
                continue
            code = normalize_code(str(row.get("code") or row.get("symbol") or ""))
            qty = int(row.get("quantity") or row.get("volume") or row.get("avail_qty") or 0)
            if code:
                positions[code] = qty

    quote = client.get_quote(signal.code)
    price = quote_last_price(quote or {})
    if price is None or price <= 0:
        raise RuntimeError(f"quote price unavailable for {signal.code}")

    target_value = _signal_target_value(signal, account)
    code = normalize_code(signal.code)
    current_qty = int(positions.get(code, 0))
    target_qty = lot_target_quantity(target_value, price, lot_size=lot_size)
    delta_qty = target_qty - current_qty
    if delta_qty == 0:
        return None

    side = "buy" if delta_qty > 0 else "sell"
    quantity = abs(int(delta_qty))
    price_mode = str(signal.price_mode or "latest").lower()
    price_type = "latest" if price_mode == "latest" else "limit"
    order_price = None if price_type == "latest" else round_price_for_code(code, price)
    return {
        "code": code,
        "side": side,
        "quantity": quantity,
        "price": order_price,
        "price_type": price_type,
        "strategy_name": signal.strategy_name,
        "order_remark": f"{signal.leg_name}:{signal.note}".strip(":"),
        "client_order_id": signal.client_order_id,
        "target_value": target_value,
        "current_qty": current_qty,
        "target_qty": target_qty,
        "quote_price": price,
    }


def main() -> int:
    args = parse_args()
    if not args.redis_url:
        raise SystemExit("--redis-url is required")

    cfg = load_config(args.config)
    gw = resolve_gateway_settings(cfg)
    client = GatewayClient(
        base_url=gw.base_url,
        token=gw.token,
        timeout_sec=gw.timeout_sec,
        account_id=gw.account_id,
    )
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)
    queue_key = str(args.queue_key)
    processing_key = f"{queue_key}:processing"
    hmac_secret = str(args.hmac_secret or "")
    max_retries = max(0, int(args.max_retries))

    processed = 0
    while True:
        raw = r.brpoplpush(queue_key, processing_key, timeout=max(1, int(args.poll_timeout)))
        if raw is None:
            if args.once:
                break
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("signal payload must be dict")
            if not verify_signal_signature(payload, hmac_secret):
                raise ValueError("invalid or missing signal signature")
            signal = JqSignal.from_payload(payload)
            if signal_is_stale(signal.generated_at, args.stale_after_sec):
                raise ValueError("stale signal")
            idem_key = f"{queue_key}:processed:{signal.client_order_id}"
            inflight_key = f"{queue_key}:inflight:{signal.client_order_id}"
            retry_key = f"{queue_key}:retry:{signal.client_order_id}"
            if r.exists(idem_key):
                print(f"[SKIP] duplicate {signal.client_order_id}")
            elif not r.set(inflight_key, "1", nx=True, ex=max(30, int(args.inflight_ttl_sec))):
                _retry_later(raw, queue_key, r)
                print(f"[DEFER] in-flight duplicate {signal.client_order_id}")
            else:
                try:
                    plan = _build_order_plan(signal, client, lot_size=args.lot_size)
                    if plan is None:
                        print(f"[SKIP] no delta for {signal.code} id={signal.client_order_id}")
                    elif args.dry_run:
                        print(f"[DRYRUN] {json.dumps(plan, ensure_ascii=False)}")
                    else:
                        res = client.submit_order(**{k: plan[k] for k in ("code", "side", "quantity", "price", "price_type", "strategy_name", "order_remark", "client_order_id")})
                        print(f"[ORDER] {json.dumps({'signal': signal.to_payload(), 'plan': plan, 'result': res}, ensure_ascii=False)}")
                    # mark processed only after successful handling (or no-op)
                    if not args.dry_run:
                        r.set(idem_key, "1", ex=max(60, int(args.idempotency_ttl_sec)))
                    r.delete(retry_key)
                finally:
                    r.delete(inflight_key)
            r.lrem(processing_key, 1, raw)
            processed += 1
            if args.once and processed >= 1:
                break
        except Exception as exc:
            r.lrem(processing_key, 1, raw)
            try:
                payload = json.loads(raw)
                client_order_id = str(payload.get("client_order_id") or "")
            except Exception:
                client_order_id = ""
            if client_order_id:
                r.delete(f"{queue_key}:inflight:{client_order_id}")
                retry_key = f"{queue_key}:retry:{client_order_id}"
                retries = r.incr(retry_key)
                r.expire(retry_key, max(300, int(args.idempotency_ttl_sec)))
            else:
                retries = max_retries + 1
            if retries <= max_retries:
                _retry_later(raw, queue_key, r)
                print(f"[RETRY] {exc} retry={retries}/{max_retries}")
            else:
                _dead_letter(raw, queue_key, r, str(exc))
                print(f"[ERROR] {exc}")
            if args.once:
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
