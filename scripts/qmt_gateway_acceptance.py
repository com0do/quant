#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class StepResult:
    name: str
    level: str  # green / yellow / red
    detail: str


class GatewayChecker:
    def __init__(self, base_url: str, token: str, timeout_sec: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = max(1, int(timeout_sec))
        self.s = requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "X-Token": token}

    def get(self, path: str) -> Any:
        resp = self.s.get(f"{self.base_url}{path}", headers=self.headers, timeout=self.timeout_sec)
        resp.raise_for_status()
        obj = resp.json()
        if not obj.get("success", False):
            raise RuntimeError(f"{path} failed: {obj}")
        return obj.get("data")

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        resp = self.s.post(
            f"{self.base_url}{path}",
            json=payload or {},
            headers=self.headers,
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        obj = resp.json()
        if not obj.get("success", False):
            raise RuntimeError(f"{path} failed: {obj}")
        return obj.get("data")


def _green(name: str, detail: str) -> StepResult:
    return StepResult(name=name, level="green", detail=detail)


def _yellow(name: str, detail: str) -> StepResult:
    return StepResult(name=name, level="yellow", detail=detail)


def _red(name: str, detail: str) -> StepResult:
    return StepResult(name=name, level="red", detail=detail)


def run_check(
    base_url: str,
    token: str,
    account_id: str,
    quote_code: str,
    timeout_sec: int,
    do_test_order: bool,
    order_code: str,
    order_qty: int,
    order_price: float,
) -> tuple[list[StepResult], int]:
    c = GatewayChecker(base_url=base_url, token=token, timeout_sec=timeout_sec)
    out: list[StepResult] = []
    worst = 0  # 0 green, 1 yellow, 2 red

    def add(res: StepResult) -> None:
        nonlocal worst
        out.append(res)
        if res.level == "yellow":
            worst = max(worst, 1)
        elif res.level == "red":
            worst = max(worst, 2)

    # 1) health/live
    try:
        d = c.get("/health/live")
        add(_green("health/live", f"ok time={d.get('time', '')}"))
    except Exception as exc:
        add(_red("health/live", str(exc)))
        return out, 2

    # 2) health/trader
    try:
        d = c.get("/health/trader")
        alive = bool(d.get("alive", False))
        if alive:
            add(_green("health/trader", f"alive reconnect_count={d.get('reconnect_count', 0)}"))
        else:
            add(_red("health/trader", f"not alive reason={d.get('reason', '')}"))
            return out, 2
    except Exception as exc:
        add(_red("health/trader", str(exc)))
        return out, 2

    # 3) health/quote
    try:
        d = c.get("/health/quote")
        ok = bool(d.get("ok", False))
        age = d.get("quote_age_sec")
        if ok:
            add(_green("health/quote", f"ok age={age}s"))
        else:
            add(_yellow("health/quote", f"not fresh reason={d.get('reason', '')} age={age}s"))
    except Exception as exc:
        add(_yellow("health/quote", str(exc)))

    # 4) quote pull
    try:
        q = c.post("/quote", {"code": quote_code, "account_id": account_id} if account_id else {"code": quote_code})
        last = q.get("last") or q.get("last_price") or q.get("price")
        add(_green("quote", f"{quote_code} last={last}"))
    except Exception as exc:
        add(_red("quote", str(exc)))

    # 5) account
    try:
        a = c.post("/account", {"account_id": account_id} if account_id else {})
        cash = a.get("cash")
        equity = a.get("equity", a.get("total_asset"))
        add(_green("account", f"cash={cash} equity={equity}"))
    except Exception as exc:
        add(_red("account", str(exc)))

    # 6) positions
    try:
        p = c.post("/positions", {"account_id": account_id} if account_id else {})
        n = len(p) if isinstance(p, list) else (len(p.keys()) if isinstance(p, dict) else 0)
        add(_green("positions", f"rows={n}"))
    except Exception as exc:
        add(_red("positions", str(exc)))

    # 7) optional test order (small lot)
    if do_test_order:
        try:
            coid = f"acc-{int(time.time())}"
            od = c.post(
                "/order",
                {
                    "code": order_code,
                    "side": "buy",
                    "quantity": int(order_qty),
                    "price": float(order_price),
                    "price_type": "limit",
                    "client_order_id": coid,
                    "account_id": account_id,
                    "order_remark": "gateway_acceptance",
                },
            )
            oid = str(od.get("order_id", ""))
            if oid:
                add(_green("order", f"submitted order_id={oid}"))
                st = c.post("/order/status", {"order_id": oid, "account_id": account_id} if account_id else {"order_id": oid})
                add(_green("order/status", f"status={st.get('status', st.get('order_status', 'unknown'))}"))
            else:
                add(_yellow("order", f"submitted but empty order_id payload={od}"))
        except Exception as exc:
            add(_red("order", str(exc)))

    # 8) metrics
    try:
        m = c.get("/metrics/summary")
        ep = m.get("api", {}) if isinstance(m, dict) else {}
        add(_green("metrics/summary", f"endpoints={len(ep)} reconnect_count={m.get('reconnect_count', 0)}"))
    except Exception as exc:
        add(_yellow("metrics/summary", str(exc)))

    return out, worst


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QMT gateway acceptance checklist runner")
    p.add_argument("--base-url", required=True, help="Gateway URL, e.g. http://127.0.0.1:18080")
    p.add_argument("--token", required=True, help="Gateway token")
    p.add_argument("--account-id", default="", help="Optional account id")
    p.add_argument("--quote-code", default="000001.SZ", help="Probe code for quote")
    p.add_argument("--timeout-sec", type=int, default=8)
    p.add_argument("--test-order", action="store_true", help="Enable small test order")
    p.add_argument("--order-code", default="000001.SZ", help="Test order code")
    p.add_argument("--order-qty", type=int, default=100, help="Test order quantity")
    p.add_argument("--order-price", type=float, default=10.0, help="Test limit price")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    results, worst = run_check(
        base_url=args.base_url,
        token=args.token,
        account_id=args.account_id,
        quote_code=args.quote_code,
        timeout_sec=args.timeout_sec,
        do_test_order=bool(args.test_order),
        order_code=args.order_code,
        order_qty=int(args.order_qty),
        order_price=float(args.order_price),
    )
    print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    if worst >= 2:
        return 2
    if worst == 1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
