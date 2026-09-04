"""Daily archive of market news and theme data, kept for later point-in-time research (runs on the home data node).

None of these sources offers a long history, so the archive is the only way to backtest news-driven ideas later.
Sources (each is optional; a failing source does not stop the others):
  ths_limit_up  Tonghuashun limit-up pool with the editors' limit-up reason, first/last seal time and board count.
                The site serves about one year back, so every run (re)fetches up to --backfill-days of missing days.
  em_zt_pool    Eastmoney limit-up pool (first-seal time, consecutive boards, industry); only today is served.
  em_fast       Eastmoney 7x24 news flashes (with the stocks / boards each flash mentions).
  sina_7x24     Sina 7x24 news flashes.
  irm_qa        Shenzhen "Hudongyi" investor Q&A that the company has answered, filed by answer time.
  concept_tags  Eastmoney thematic concept tags of the universe stocks, a snapshot at most once a week.

Files: data/news/<source>/<YYYY-MM-DD>.jsonl (one JSON object per line, deduplicated by id) and
data/news/concept_tags/<YYYY-MM-DD>.csv.gz. data/ is not committed.

usage: uv run python scripts/news_archive.py [--sources ths_limit_up,em_fast] [--lookback-hours 30] [--backfill-days 400]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0"}
THS_URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
THS_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"
EM_ZT_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
EM_FAST_URL = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
IRM_URL = "https://irm.cninfo.com.cn/newircs/index/search"
EM_F10_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
DEFAULT_DBS = ("data/runtime/market_csi1000_latest.db", "data/runtime/market_csi2000_latest.db")


def _get_json(sess: requests.Session, method: str, url: str, tries: int = 3, **kw) -> dict:
    for i in range(tries):
        try:
            r = sess.request(method, url, timeout=25, **kw)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{method} {url} failed after {tries} tries")


def _write_jsonl(path: Path, rows: list[dict], key: str, replace: bool = False) -> int:
    """Merge rows into a JSONL file by `key` (replace=True overwrites the file); returns the number of new keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old: dict[str, dict] = {}
    if path.exists() and not replace:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                old[str(row[key])] = row
    before = set(old)
    for row in rows:
        old[str(row[key])] = row
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old.values()), encoding="utf-8")
    tmp.replace(path)
    return len(set(old) - before) if not replace else len(old)


def _by_day(rows: list[dict], ts_key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r[ts_key])[:10], []).append(r)
    return out


def ths_limit_up(sess: requests.Session, out: Path, backfill_days: int, **_) -> str:
    """One file per trading day; the last 3 calendar days are always refetched because they may have been partial."""
    today, fetched, rows_total, invalid = date.today(), 0, 0, 0
    for i in range(backfill_days):
        day = today - timedelta(days=i)
        path = out / "ths_limit_up" / f"{day.isoformat()}.jsonl"
        if day.weekday() >= 5 or (path.exists() and i > 3):
            continue
        rows, page = [], 1
        while True:
            d = _get_json(sess, "GET", THS_URL, params={
                "page": page, "limit": 200, "field": THS_FIELDS, "filter": "HS,GEM2STAR",
                "order_field": "330324", "order_type": 0, "date": day.strftime("%Y%m%d")})
            if d.get("status_code") != 0:
                invalid += 1
                break
            data = d.get("data") or {}
            rows += [{**x, "date": day.isoformat()} for x in data.get("info") or []]
            if page >= ((data.get("page") or {}).get("count") or 1):
                break
            page += 1
        if invalid >= 5:
            break
        if rows:
            invalid = 0
            _write_jsonl(path, rows, "code", replace=True)
            fetched, rows_total = fetched + 1, rows_total + len(rows)
        time.sleep(0.3)
    return f"{fetched} days written, {rows_total} rows"


def em_zt_pool(sess: requests.Session, out: Path, **_) -> str:
    """Only the latest trading day is served (qdate); before the open that is the previous session."""
    def fetch(day: str) -> dict:
        return _get_json(sess, "GET", EM_ZT_URL, params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                                                         "Pageindex": 0, "pagesize": 1000, "sort": "fbt:asc",
                                                         "date": day}).get("data") or {}

    data = fetch(date.today().strftime("%Y%m%d"))
    if not data.get("pool") and data.get("qdate"):
        data = fetch(str(data["qdate"]))
    if not data.get("pool"):
        return "no pool served"
    day = datetime.strptime(str(data["qdate"]), "%Y%m%d").date().isoformat()
    rows = [{**x, "date": day} for x in data["pool"]]
    _write_jsonl(out / "em_zt_pool" / f"{day}.jsonl", rows, "c", replace=True)
    return f"{len(rows)} rows for {day}"


def em_fast(sess: requests.Session, out: Path, cutoff: datetime, **_) -> str:
    rows, cursor = [], ""
    for _page in range(40):
        d = _get_json(sess, "GET", EM_FAST_URL, params={"client": "web", "biz": "web_724", "fastColumn": 102,
                                                        "sortEnd": cursor, "pageSize": 200, "req_trace": int(time.time())})
        data = d.get("data") or {}
        items = data.get("fastNewsList") or []
        rows += [{k: x.get(k) for k in ("code", "showTime", "title", "summary", "stockList")} for x in items]
        if not items or items[-1]["showTime"] < cutoff.strftime("%Y-%m-%d %H:%M:%S"):
            break
        cursor = data.get("sortEnd") or ""
        time.sleep(0.3)
    new = sum(_write_jsonl(out / "em_fast" / f"{day}.jsonl", part, "code") for day, part in _by_day(rows, "showTime").items())
    return f"{len(rows)} fetched, {new} new"


def sina_7x24(sess: requests.Session, out: Path, cutoff: datetime, **_) -> str:
    rows = []
    for page in range(1, 60):
        d = _get_json(sess, "GET", SINA_URL, params={"page": page, "page_size": 100, "zhibo_id": 152, "tag_id": 0,
                                                     "dire": "f", "dpc": 1})
        items = ((((d.get("result") or {}).get("data") or {}).get("feed") or {}).get("list")) or []
        rows += [{"id": x["id"], "create_time": x["create_time"], "rich_text": x.get("rich_text"),
                  "tags": [t.get("name") for t in x.get("tag") or []]} for x in items]
        if not items or items[-1]["create_time"] < cutoff.strftime("%Y-%m-%d %H:%M:%S"):
            break
        time.sleep(0.3)
    new = sum(_write_jsonl(out / "sina_7x24" / f"{day}.jsonl", part, "id") for day, part in _by_day(rows, "create_time").items())
    return f"{len(rows)} fetched, {new} new"


def irm_qa(sess: requests.Session, out: Path, cutoff: datetime, **_) -> str:
    rows, cut_ms = [], cutoff.timestamp() * 1000
    for page in range(1, 40):
        d = _get_json(sess, "POST", IRM_URL, data={"pageNo": page, "pageSize": 100, "searchTypes": "11", "keyWord": "",
                                                   "market": "", "industry": "", "stockCode": ""})
        items = d.get("results") or []
        for x in items:
            answered = int(x.get("attachedPubDate") or x.get("updateDate") or 0)
            rows.append({"id": x["esId"], "code": x.get("stockCode"), "name": x.get("companyShortName"),
                         "asked": datetime.fromtimestamp(int(x["pubDate"]) / 1000).isoformat(sep=" "),
                         "answered": datetime.fromtimestamp(answered / 1000).isoformat(sep=" "),
                         "question": x.get("mainContent"), "answer": x.get("attachedContent"), "trade": x.get("trade")})
        if not items or int(items[-1].get("updateDate") or 0) < cut_ms:
            break
        time.sleep(0.3)
    new = sum(_write_jsonl(out / "irm_qa" / f"{day}.jsonl", part, "id") for day, part in _by_day(rows, "answered").items())
    return f"{len(rows)} fetched, {new} new"


def _universe_codes() -> list[str]:
    dbs = [os.getenv("JQ_SYNC_CSI1000_DB") or DEFAULT_DBS[0], os.getenv("JQ_SYNC_CSI2000_DB") or DEFAULT_DBS[1]]
    codes: set[str] = set()
    for db in dbs:
        p = Path(db) if Path(db).is_absolute() else REPO / db
        if p.exists():
            with sqlite3.connect(p) as con:
                codes |= {r[0] for r in con.execute("select distinct code from prices_daily")}
    return sorted(codes)


def concept_tags(sess: requests.Session, out: Path, **_) -> str:
    folder = out / "concept_tags"
    folder.mkdir(parents=True, exist_ok=True)
    recent = sorted(folder.glob("*.csv.gz"))
    if recent and (date.today() - date.fromisoformat(recent[-1].name[:10])).days < 7:
        return f"skipped, last snapshot {recent[-1].name[:10]}"
    codes = _universe_codes()
    if not codes:
        return "no universe database found"

    def one(code: str) -> list[str]:
        num, _, suffix = code.partition(".")
        sym = {"XSHG": "SH", "XSHE": "SZ"}.get(suffix, "BJ") + num
        d = _get_json(sess, "GET", EM_F10_URL, params={"code": sym})
        return [f"{code},BK{int(x['BOARD_CODE']):04d},{x['BOARD_NAME']},{x.get('IS_PRECISE') or ''}" for x in d.get("ssbk") or []]

    lines, failed = ["code,bk,name,precise"], 0
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda c: _safe(one, c), codes):
            if res is None:
                failed += 1
            else:
                lines += res
    path = folder / f"{date.today().isoformat()}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return f"{len(codes) - failed} of {len(codes)} stocks, {len(lines) - 1} tags"


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


SOURCES = {"ths_limit_up": ths_limit_up, "em_zt_pool": em_zt_pool, "em_fast": em_fast, "sina_7x24": sina_7x24,
           "irm_qa": irm_qa, "concept_tags": concept_tags}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(SOURCES))
    ap.add_argument("--out", default=str(REPO / "data" / "news"))
    ap.add_argument("--lookback-hours", type=float, default=30)
    ap.add_argument("--backfill-days", type=int, default=400)
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    sess = requests.Session()
    sess.headers.update(UA)
    out, cutoff = Path(args.out), datetime.now() - timedelta(hours=args.lookback_hours)
    failures = 0
    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    for name in names:
        t0 = time.time()
        try:
            msg = SOURCES[name](sess, out, cutoff=cutoff, backfill_days=args.backfill_days)
        except Exception as exc:
            failures += 1
            msg = f"FAILED: {exc}"
        print(f"[news-archive] {name}: {msg} ({time.time() - t0:.0f}s)", flush=True)
    return 1 if failures == len(names) else 0


if __name__ == "__main__":
    sys.exit(main())
