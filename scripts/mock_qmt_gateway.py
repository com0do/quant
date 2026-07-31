#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from uuid import uuid4


ACCOUNT = {"cash": 1_000_000.0, "equity": 1_000_000.0}
POSITIONS: dict[str, int] = {}
ORDERS: dict[str, dict[str, Any]] = {}


def _reply(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    token: str = ""

    def _check_auth(self) -> bool:
        if not self.token:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8", errors="replace")
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            _reply(self, 401, {"ok": False, "error": "unauthorized"})
            return

        payload = self._read_json()
        path = self.path.rstrip("/")
        if path == "/account":
            _reply(self, 200, {"data": ACCOUNT})
            return
        if path == "/positions":
            _reply(self, 200, {"data": POSITIONS})
            return
        if path == "/quote":
            code = str(payload.get("code") or "")
            _reply(self, 200, {"data": {"code": code, "last": 10.0}})
            return
        if path == "/order":
            code = str(payload.get("code") or "")
            side = str(payload.get("side") or "buy").lower()
            qty = int(payload.get("quantity") or 0)
            px = float(payload.get("price") or 10.0)
            oid = uuid4().hex[:16]
            status = "filled" if qty > 0 and code else "rejected"
            if status == "filled":
                if side == "buy":
                    ACCOUNT["cash"] -= qty * px
                    POSITIONS[code] = POSITIONS.get(code, 0) + qty
                else:
                    hold = POSITIONS.get(code, 0)
                    sold = min(hold, qty)
                    POSITIONS[code] = hold - sold
                    ACCOUNT["cash"] += sold * px
            ACCOUNT["equity"] = ACCOUNT["cash"]
            od = {
                "order_id": oid,
                "code": code,
                "side": side,
                "quantity": qty,
                "price": px,
                "status": status,
                "filled_qty": (qty if status == "filled" else 0),
                "avg_fill_price": (px if status == "filled" else 0.0),
            }
            ORDERS[oid] = od
            _reply(self, 200, {"data": od})
            return
        if path == "/cancel":
            oid = str(payload.get("order_id") or "")
            ok = oid in ORDERS
            if ok:
                ORDERS[oid]["status"] = "canceled"
            _reply(self, 200, {"data": {"ok": ok}})
            return
        if path == "/order/status":
            oid = str(payload.get("order_id") or "")
            _reply(self, 200, {"data": ORDERS.get(oid, {})})
            return
        if path == "/fills":
            fills = [v for v in ORDERS.values() if str(v.get("status")) == "filled"]
            _reply(self, 200, {"data": fills})
            return
        _reply(self, 404, {"ok": False, "error": f"unknown path: {self.path}"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mock QMT HTTP gateway for integration testing")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18080)
    p.add_argument("--token", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    Handler.token = str(args.token or "")
    server = HTTPServer((args.host, args.port), Handler)
    print(f"mock qmt gateway listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
