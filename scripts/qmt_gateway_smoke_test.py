#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from quant.execution.qmt_http_broker import QmtHttpBroker


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QMT HTTP gateway smoke test")
    p.add_argument("--base-url", required=True, help="Gateway base url, e.g. http://127.0.0.1:18080")
    p.add_argument("--token", default="", help="Bearer token")
    p.add_argument("--account-id", default="", help="Broker account id")
    p.add_argument("--timeout-sec", type=int, default=8, help="HTTP timeout in seconds")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    broker = QmtHttpBroker(
        base_url=args.base_url,
        token=args.token,
        timeout_sec=args.timeout_sec,
        account_id=args.account_id,
    )
    out: dict[str, object] = {"status": "ok"}
    try:
        out["account"] = broker.get_account()
        out["positions"] = broker.get_positions()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
