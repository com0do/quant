"""Data helpers for the strong-dragon study: case table, daily bars with an on-disk cache, limit-up flags.

Daily bars come from JQData when its server is reachable, otherwise from public web quotes
(Tencent forward-adjusted bars for Shanghai/Shenzhen, Sina unadjusted bars for Beijing).
The cache lives in output/strong_dragon/cache/ so repeated runs make no network calls.
"""
from __future__ import annotations

import os
import socket
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "output" / "strong_dragon"
CACHE = OUT / "cache"
JQ_HOST, JQ_PORT = "39.107.190.114", 7000


def load_cases() -> pd.DataFrame:
    df = pd.read_csv(HERE / "cases.csv", dtype={"claimed_days": "Int64", "chart_bars": "Int64"})
    for c in ("claimed_start", "claimed_end", "chart_start", "chart_end"):
        df[c] = pd.to_datetime(df[c])
    return df


def limit_pct(code: str, name: str = "") -> float:
    """Daily price-limit ratio of the board the stock trades on."""
    if "ST" in (name or "").upper():
        return 0.05
    num, _, suffix = code.partition(".")
    if suffix == "BJ" or num.startswith(("920", "8", "4")):
        return 0.30
    if num.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def add_flags(df: pd.DataFrame, code: str, name: str = "") -> pd.DataFrame:
    """Add pct change and limit-up flags. One-word limit-up = open == high == low == close at the limit."""
    df = df.sort_values("date").reset_index(drop=True).copy()
    df["pre_close"] = df["close"].shift(1)
    df["pct"] = df["close"] / df["pre_close"] - 1
    lim = limit_pct(code, name)
    # A 0.5 pp tolerance absorbs rounding of adjusted prices.
    df["limit_up"] = df["pct"] >= lim - 0.005
    flat = (df["high"] - df["low"]).abs() <= df["close"] * 1e-4
    df["one_word_up"] = df["limit_up"] & flat
    return df


def _jq_reachable(timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((JQ_HOST, JQ_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _load_env_proxy() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except Exception:
        pass


def _fetch_jq(code: str, start: str, end: str) -> pd.DataFrame:
    import warnings
    warnings.filterwarnings("ignore")
    from quant.stock_data.jq_client import JqClient
    with JqClient().session() as jq:
        df = jq.get_price(code, start_date=start, end_date=end, frequency="daily",
                          fields=["open", "high", "low", "close", "volume", "paused"],
                          skip_paused=True, fq="pre", panel=False)
    df = df.reset_index().rename(columns={"index": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "open", "high", "low", "close", "volume"]]


def _fetch_tencent(code: str, start: str, end: str) -> pd.DataFrame:
    import requests
    num, _, suffix = code.partition(".")
    sym = ("sh" if suffix == "XSHG" else "sz") + num
    r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": f"{sym},day,{start},{end},640,qfq"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    node = (r.json().get("data") or {}).get(sym) or {}
    rows = node.get("qfqday") or node.get("day") or []
    df = pd.DataFrame([x[:6] for x in rows], columns=["date", "open", "close", "high", "low", "volume"])
    return df


def _fetch_sina(code: str, start: str, end: str) -> pd.DataFrame:
    import requests
    num = code.partition(".")[0]
    r = requests.get("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                     params={"symbol": "bj" + num, "scale": 240, "ma": "no", "datalen": 1023},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    rows = r.json() if r.text.strip().startswith("[") else []
    df = pd.DataFrame(rows).rename(columns={"day": "date"})
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df[["date", "open", "high", "low", "close", "volume"]]


@lru_cache(maxsize=None)
def _source() -> str:
    pref = os.environ.get("SD_SOURCE", "auto")
    if pref != "auto":
        return pref
    return "jq" if _jq_reachable() else "web"


def daily_bars(code: str, start: str, end: str, name: str = "") -> pd.DataFrame:
    """Daily OHLCV between start and end (inclusive), cached per (code, start, end)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{code}_{start}_{end}.csv"
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
    else:
        _load_env_proxy()
        src = _source()
        if src == "jq":
            df, how = _fetch_jq(code, start, end), "jqdata"
        elif code.endswith(".BJ"):
            df, how = _fetch_sina(code, start, end), "sina_unadjusted"
        else:
            df, how = _fetch_tencent(code, start, end), "tencent_qfq"
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["source"] = how
        df.to_csv(path, index=False)
        time.sleep(0.4)
    return add_flags(df, code, name)
