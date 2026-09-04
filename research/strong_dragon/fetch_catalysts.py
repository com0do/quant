"""Fetch the news-side inputs of the catalyst study: company announcements and concept (theme) boards.

Announcements: cninfo (the exchanges' official disclosure site), every announcement title and date for each stock
that has a fresh RED in the universe (2024-12..2026-03) plus the deck's case stocks (2023-09..2026-03).
Earnings forecasts: Eastmoney's forecast table (forecast type, e.g. "up sharply" / "back to profit", and the notice
date), all A shares.
Concepts: each universe stock's Eastmoney board tags (F10 "core concept" page; IS_PRECISE = 1 marks thematic
concepts). Concept heat is computed later from the local price database, because Eastmoney's quote hosts
(push2 / push2his) block bulk requests from this network. The tags are today's snapshot, so a stock that joined a
theme after its rally carries that theme back in time (a bias in favour of the concept hypothesis).

Everything is cached under output/strong_dragon/cache/{ann,concept}/, so reruns only fetch what is missing.
usage: uv run python research/strong_dragon/fetch_catalysts.py [--only ann|concept|forecast] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from sd_data import CACHE, OUT, REPO, _load_env_proxy, load_cases

ANN_DIR = CACHE / "ann"
CON_DIR = CACHE / "concept"
UA = {"User-Agent": "Mozilla/5.0"}
UNIVERSE_RANGE = ("2024-12-01", "2026-03-20")
CASE_RANGE = ("2023-09-01", "2026-03-31")
EM_F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DBS = (REPO / "data" / "runtime" / "market_csi1000_latest.db", REPO / "data" / "runtime" / "market_csi2000_latest.db")
FORECAST_PERIODS = ("2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31",
                    "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31")

def _get_json(method: str, url: str, tries: int = 4, **kw) -> dict:
    for i in range(tries):
        try:
            r = requests.request(method, url, headers=UA, timeout=25, **kw)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{method} {url} failed after {tries} tries")


def org_ids() -> dict[str, str]:
    path = CACHE / "cninfo_orgid.json"
    if path.exists():
        return json.loads(path.read_text())
    out = {}
    for f in ("szse_stock.json", "bj_stock.json"):
        for x in _get_json("GET", f"https://www.cninfo.com.cn/new/data/{f}")["stockList"]:
            out[x["code"]] = x["orgId"]
    path.write_text(json.dumps(out))
    return out


def _column(num: str) -> str:
    if num.startswith(("6",)):
        return "sse"
    if num.startswith(("0", "3")):
        return "szse"
    return "bj"


def fetch_announcements(code: str, org: str, start: str, end: str) -> int:
    path = ANN_DIR / f"{code}_{start}_{end}.csv"
    if path.exists():
        return 0
    num = code.partition(".")[0]
    rows, page = [], 1
    while True:
        d = _get_json("POST", "https://www.cninfo.com.cn/new/hisAnnouncement/query", data={
            "pageNum": page, "pageSize": 30, "column": _column(num), "tabName": "fulltext", "plate": "",
            "stock": f"{num},{org}", "searchkey": "", "secid": "", "category": "", "trade": "",
            "seDate": f"{start}~{end}", "sortName": "", "sortType": "", "isHLtitle": "true"})
        for a in d.get("announcements") or []:
            rows.append({"code": code, "ts": a["announcementTime"], "title": re.sub(r"<[^>]+>", "", a["announcementTitle"]),
                         "ann_id": a["announcementId"]})
        if not d.get("hasMore"):
            break
        page += 1
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["code", "ts", "title", "ann_id"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    tmp = path.with_suffix(".tmp")
    df.drop(columns="ts").drop_duplicates("ann_id").to_csv(tmp, index=False)
    tmp.replace(path)
    return len(df)


def announcement_jobs() -> list[tuple[str, str, str]]:
    trades = pd.read_csv(OUT / "sector_trades_all.csv", usecols=["code"])
    jobs = [(c, *UNIVERSE_RANGE) for c in sorted(trades["code"].unique())]
    jobs += [(c, *CASE_RANGE) for c in sorted(load_cases()["code"].unique())]
    return jobs


def run_announcements(workers: int = 6) -> None:
    ANN_DIR.mkdir(parents=True, exist_ok=True)
    orgs = org_ids()
    jobs = [j for j in announcement_jobs() if not (ANN_DIR / f"{j[0]}_{j[1]}_{j[2]}.csv").exists()]
    missing = [j for j in jobs if j[0].partition(".")[0] not in orgs]
    jobs = [j for j in jobs if j[0].partition(".")[0] in orgs]
    print(f"announcements: {len(jobs)} stock windows to fetch, {len(missing)} without a cninfo orgId")
    _pool(lambda j: fetch_announcements(j[0], orgs[j[0].partition(".")[0]], j[1], j[2]), jobs, workers, "announcements")
    print(f"announcement files cached: {len(list(ANN_DIR.glob('*.csv')))}")


def _f10_symbol(code: str) -> str:
    num, _, suffix = code.partition(".")
    return {"XSHG": "SH", "XSHE": "SZ"}.get(suffix, "BJ") + num


def fetch_tags(code: str) -> list[dict]:
    path = CON_DIR / "tags" / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text())
    d = _get_json("GET", EM_F10, params={"code": _f10_symbol(code)})
    tags = [{"code": code, "bk": f"BK{int(x['BOARD_CODE']):04d}", "name": x["BOARD_NAME"], "precise": x.get("IS_PRECISE")}
            for x in d.get("ssbk") or []]
    path.write_text(json.dumps(tags, ensure_ascii=False))
    return tags


def universe_codes() -> list[str]:
    codes = set()
    for db in DBS:
        with sqlite3.connect(db) as con:
            codes |= {r[0] for r in con.execute("select distinct code from prices_daily")}
    return sorted(codes)


def _pool(fn, items, workers: int, label: str) -> None:
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn, x): x for x in items}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as exc:
                print(f"[warn] {label} {futs[f]}: {exc}")
            done += 1
            if done % 300 == 0:
                print(f"  {label}: {done}/{len(items)}")


def run_concepts(workers: int = 4) -> None:
    (CON_DIR / "tags").mkdir(parents=True, exist_ok=True)
    codes = sorted(set(universe_codes()) | {c for c, _, _ in announcement_jobs()})
    _pool(fetch_tags, [c for c in codes if not (CON_DIR / "tags" / f"{c}.json").exists()], workers, "tags")
    tags = pd.DataFrame([t for c in codes if (CON_DIR / "tags" / f"{c}.json").exists()
                         for t in json.loads((CON_DIR / "tags" / f"{c}.json").read_text())])
    tags.to_csv(CON_DIR / "tags.csv", index=False)
    print(f"tags: {len(tags)} rows over {tags['code'].nunique()} of {len(codes)} stocks, "
          f"{tags.loc[tags['precise'].astype(str) == '1', 'bk'].nunique()} thematic concepts")


def run_forecasts() -> None:
    """Earnings forecasts (the direction is not in the announcement title, so take it from Eastmoney's table)."""
    path = CACHE / "forecast.csv"
    if path.exists():
        return
    rows = []
    for period in FORECAST_PERIODS:
        page = 1
        while True:
            r = _get_json("GET", EM_DATACENTER, params={
                "reportName": "RPT_PUBLIC_OP_NEWPREDICT", "columns": "SECURITY_CODE,NOTICE_DATE,REPORT_DATE,PREDICT_TYPE,PREDICT_FINANCE",
                "pageNumber": page, "pageSize": 500, "sortColumns": "NOTICE_DATE", "sortTypes": -1,
                "filter": f"(REPORT_DATE='{period}')"}).get("result") or {}
            rows += r.get("data") or []
            if page >= (r.get("pages") or 0):
                break
            page += 1
            time.sleep(0.2)
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"forecasts: {len(rows)} rows over {len(FORECAST_PERIODS)} report periods")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ann", "concept", "forecast"])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    _load_env_proxy()
    if args.only in (None, "forecast"):
        run_forecasts()
    if args.only in (None, "concept"):
        run_concepts(args.workers)
    if args.only in (None, "ann"):
        run_announcements(args.workers)


if __name__ == "__main__":
    main()
