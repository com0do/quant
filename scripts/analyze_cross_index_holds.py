#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.stock_data.index_constituents import IndexConstituentsDB


@dataclass
class Lot:
    buy_date: pd.Timestamp
    shares: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze cross-index holding durations from trades.")
    p.add_argument("--trades-csv", default="output/backtest_trades.csv")
    p.add_argument("--constituents-db", default="data/meta/index_constituents.db")
    p.add_argument("--from-index", default="000852.XSHG")
    p.add_argument("--to-index", default="000905.XSHG")
    p.add_argument("--output-json", default="output/cross_index_hold_report.json")
    p.add_argument("--output-md", default="reports/cross_index_hold_report.md")
    return p.parse_args()


def _index_on(cdb: IndexConstituentsDB, code: str, date_str: str) -> set[str]:
    return set(cdb.get_indexes_for_code(code=code, asof_date=date_str))


def main() -> None:
    args = parse_args()
    trades_path = Path(args.trades_csv)
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    if not trades_path.exists():
        out = {"status": "error", "message": f"trades file not found: {trades_path}"}
        out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return

    tr = pd.read_csv(trades_path)
    if tr.empty:
        out = {"status": "ok", "message": "empty trades", "closed_lots": 0}
        out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return
    tr["date"] = pd.to_datetime(tr["date"])
    tr = tr.sort_values(["date"]).reset_index(drop=True)

    cdb = IndexConstituentsDB(args.constituents_db)
    lots: dict[str, deque[Lot]] = defaultdict(deque)
    closed_rows: list[dict] = []

    for r in tr.itertuples(index=False):
        code = str(r.code)
        side = str(r.side).upper()
        dt = pd.Timestamp(r.date)
        shares = int(r.shares)
        if side == "BUY":
            lots[code].append(Lot(buy_date=dt, shares=shares))
            continue
        if side != "SELL":
            continue
        rem = shares
        q = lots[code]
        while rem > 0 and q:
            lot = q[0]
            take = min(rem, lot.shares)
            buy_s = lot.buy_date.strftime("%Y-%m-%d")
            sell_s = dt.strftime("%Y-%m-%d")
            buy_idx = _index_on(cdb, code, buy_s)
            sell_idx = _index_on(cdb, code, sell_s)
            buy_in_from = args.from_index in buy_idx
            buy_in_to = args.to_index in buy_idx

            # Check whether the stock enters target index at any point during holding.
            moved_to_target = False
            first_target_date = ""
            for d in pd.date_range(lot.buy_date, dt, freq="D"):
                d_s = d.strftime("%Y-%m-%d")
                idx = _index_on(cdb, code, d_s)
                if args.to_index in idx:
                    moved_to_target = True
                    first_target_date = d_s
                    break
            transitioned_from_to = bool(buy_in_from and moved_to_target and (not buy_in_to) and first_target_date != buy_s)

            closed_rows.append(
                {
                    "code": code,
                    "buy_date": buy_s,
                    "sell_date": sell_s,
                    "shares": int(take),
                    "hold_days": int((dt - lot.buy_date).days),
                    "buy_indexes": sorted(buy_idx),
                    "sell_indexes": sorted(sell_idx),
                    "buy_in_from_index": buy_in_from,
                    "buy_in_to_index": buy_in_to,
                    "sell_in_to_index": args.to_index in sell_idx,
                    "moved_to_target_during_hold": moved_to_target,
                    "transitioned_from_to_during_hold": transitioned_from_to,
                    "first_target_date": first_target_date,
                }
            )
            rem -= take
            lot.shares -= take
            if lot.shares == 0:
                q.popleft()
            else:
                q[0] = lot

    x = pd.DataFrame(closed_rows)
    if x.empty:
        out = {"status": "ok", "message": "no closed lots", "closed_lots": 0}
        out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return

    moved = x[x["moved_to_target_during_hold"]]
    transitioned = x[x["transitioned_from_to_during_hold"]]
    summary = {
        "status": "ok",
        "from_index": args.from_index,
        "to_index": args.to_index,
        "closed_lots": int(len(x)),
        "codes_traded": int(x["code"].nunique()),
        "lots_moved_to_target_during_hold": int(len(moved)),
        "codes_moved_to_target_during_hold": int(moved["code"].nunique()) if not moved.empty else 0,
        "lots_transitioned_from_to_during_hold": int(len(transitioned)),
        "codes_transitioned_from_to_during_hold": int(transitioned["code"].nunique()) if not transitioned.empty else 0,
        "avg_hold_days_all_lots": float(x["hold_days"].mean()),
        "max_hold_days_all_lots": int(x["hold_days"].max()),
    }
    if not moved.empty:
        summary["avg_hold_days_moved_lots"] = float(moved["hold_days"].mean())
        summary["max_hold_days_moved_lots"] = int(moved["hold_days"].max())

    snapshots = []
    with cdb._conn() as conn:  # noqa: SLF001 - used for inspection report only
        for idx in [args.from_index, args.to_index]:
            row = conn.execute(
                """
                SELECT COUNT(*), MIN(in_date), MAX(in_date)
                FROM constituent_snapshot_compact
                WHERE index_code = ?
                """,
                (idx,),
            ).fetchone()
            snapshots.append(
                {
                    "index_code": idx,
                    "snapshot_count": int(row[0] or 0),
                    "min_date": row[1],
                    "max_date": row[2],
                }
            )
    summary["snapshot_coverage"] = snapshots

    out = {
        "summary": summary,
        "moved_lots_top": moved.sort_values(["hold_days"], ascending=False).head(50).to_dict(orient="records"),
        "transitioned_lots_top": transitioned.sort_values(["hold_days"], ascending=False).head(50).to_dict(orient="records"),
    }
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Cross-Index Hold Report",
        "",
        f"- from_index: `{args.from_index}`",
        f"- to_index: `{args.to_index}`",
        f"- closed_lots: {summary['closed_lots']}",
        f"- codes_traded: {summary['codes_traded']}",
        f"- lots_moved_to_target_during_hold: {summary['lots_moved_to_target_during_hold']}",
        f"- codes_moved_to_target_during_hold: {summary['codes_moved_to_target_during_hold']}",
        f"- lots_transitioned_from_to_during_hold: {summary['lots_transitioned_from_to_during_hold']}",
        f"- codes_transitioned_from_to_during_hold: {summary['codes_transitioned_from_to_during_hold']}",
        f"- avg_hold_days_all_lots: {summary['avg_hold_days_all_lots']:.2f}",
        f"- max_hold_days_all_lots: {summary['max_hold_days_all_lots']}",
        "",
        "## Snapshot Coverage",
    ]
    for s in snapshots:
        lines.append(
            f"- {s['index_code']}: snapshots={s['snapshot_count']}, min={s['min_date']}, max={s['max_date']}"
        )
    if moved.empty:
        lines += ["", "## Moved Lots", "- none found in current trades + snapshot coverage."]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] wrote {out_json} and {out_md}")


if __name__ == "__main__":
    main()

