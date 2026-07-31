from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.stock_data.jq_client import JqClient

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detect JQ account date permission window from error message.")
    p.add_argument("--probe-code", default="000852.XSHG")
    p.add_argument("--start-date", default="2000-01-01")
    p.add_argument("--end-date", default="2100-12-31")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = {
        "status": "unknown",
        "probe_code": args.probe_code,
        "requested_start": args.start_date,
        "requested_end": args.end_date,
        "allowed_start": "",
        "allowed_end": "",
        "message": "",
    }
    jq = JqClient()
    try:
        try:
            df = jq.get_price_daily(args.probe_code, args.start_date, args.end_date)
            if df is not None:
                out["status"] = "ok"
                out["message"] = "no explicit window limit in probe response"
        except Exception as exc:
            msg = str(exc)
            out["message"] = msg
            m = re.search(r"仅能获取(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})", msg)
            if m:
                out["status"] = "limited"
                out["allowed_start"] = m.group(1)
                out["allowed_end"] = m.group(2)
            else:
                out["status"] = "error"
    finally:
        try:
            jq.logout()
        except Exception:
            pass
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
