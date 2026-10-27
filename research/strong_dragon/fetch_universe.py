"""Fetch daily bars for the strong-dragon universe from Baostock.

Universe: `industry_sw.csv` (every Shanghai/Shenzhen A-share with a Shenwan industry, a current snapshot).
Bars are forward-adjusted (adjustflag=2) daily OHLCV plus the exchange `pctChg`, `turn`, `isST` and
`tradestatus` flags, 2019-01-01 .. today. Baostock returns the whole history in one query; a process pool avoids
its process-global socket. Per-stock CSVs live in `output/strong_dragon/kline/` and make the fetch resumable.

Beijing stocks are skipped (Baostock does not cover them). The three Beijing cases in `cases.csv` are handled by
the separate case study in `sd_data.py` (Sina).

usage:
  uv run --with baostock python research/strong_dragon/fetch_universe.py --start 2019-01-01 --workers 8
Writes output/strong_dragon/kline/<code>.csv and output/strong_dragon/kline_index/<code>.csv.
"""
from __future__ import annotations

import argparse
import csv
import socket
import time
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import baostock as bs
import pandas as pd

from sd_data import HERE, OUT

KLINE = OUT / "kline"
KLINE_INDEX = OUT / "kline_index"
INDEXES = {"000852.XSHG": "sh.000852", "399303.XSHE": "sz.399303",
           "000300.XSHG": "sh.000300", "000001.XSHG": "sh.000001"}
STOCK_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg,isST,tradestatus"
INDEX_FIELDS = "date,code,open,high,low,close,volume,amount,pctChg"
START = "2019-01-01"
END = date.today().isoformat()


def bcode(code: str) -> str:
    num, _, suffix = code.partition(".")
    return ("sh." if suffix == "XSHG" else "sz.") + num


def _login() -> None:
    socket.setdefaulttimeout(45)
    for attempt in range(4):
        lg = bs.login()
        if lg.error_code == "0":
            return
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"baostock login failed: {lg.error_msg}")


def _query(code: str, start: str, end: str, fields: str, adjust: str) -> pd.DataFrame:
    rs = bs.query_history_k_data_plus(bcode(code), fields, start_date=start, end_date=end,
                                      frequency="d", adjustflag=adjust)
    if rs.error_code != "0":
        raise RuntimeError(f"query {code}: {rs.error_code} {rs.error_msg}")
    return rs.get_data()


def _write(frame: pd.DataFrame, path: Path) -> int:
    if frame.empty:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)
    return len(frame)


def fetch_stock(job: tuple[str, str, str, bool]) -> tuple[str, int | str]:
    code, start, end, force = job
    path = KLINE / f"{code}.csv"
    if path.exists() and not force:
        with path.open(encoding="utf-8") as fh:
            return code, sum(1 for _ in fh) - 1
    try:
        frame = _query(code, start, end, STOCK_FIELDS, "2")
        frame = frame[frame["tradestatus"].astype(str) == "1"]
        return code, _write(frame, path)
    except Exception as exc:
        return code, f"ERR {type(exc).__name__}: {str(exc)[:90]}"


def fetch_index(code: str, start: str, end: str) -> None:
    rs = bs.query_history_k_data_plus(INDEXES[code], INDEX_FIELDS, start_date=start, end_date=end,
                                      frequency="d", adjustflag="3")
    if rs.error_code != "0":
        raise RuntimeError(f"index {code}: {rs.error_code} {rs.error_msg}")
    frame = rs.get_data()
    _write(frame, KLINE_INDEX / f"{code}.csv")
    print(f"[index] {code}: {len(frame)} rows")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=START)
    p.add_argument("--end", default=END)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0, help="only the first N stocks (smoke test)")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()

    codes = [r["code"] for r in csv.DictReader((HERE / "industry_sw.csv").open(encoding="utf-8"))
             if not r["code"].endswith(".BJ")]
    if a.limit:
        codes = codes[:a.limit]
    print(f"[fetch] {len(codes)} stocks, {a.start} .. {a.end}, {a.workers} workers")

    _login()
    for code in INDEXES:
        fetch_index(code, a.start, a.end)

    t0 = time.time()
    ok = err = 0
    errors: list[tuple[str, str]] = []
    jobs = [(code, a.start, a.end, a.force) for code in codes]
    with Pool(a.workers, initializer=_login) as pool:
        for i, (code, result) in enumerate(pool.imap_unordered(fetch_stock, jobs, chunksize=4), 1):
            if isinstance(result, int):
                ok += 1
            else:
                err += 1
                errors.append((code, result))
            if i % 100 == 0 or i == len(codes):
                print(f"[fetch] {i}/{len(codes)} ok={ok} err={err} elapsed={time.time() - t0:.0f}s", flush=True)
    if errors:
        (OUT / "fetch_errors.csv").write_text(
            "code,error\n" + "\n".join(f"{c},{e}" for c, e in errors), encoding="utf-8")
        print(f"[fetch] {err} errors, see {OUT / 'fetch_errors.csv'}")
    print(f"[done] bars in {KLINE}, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
