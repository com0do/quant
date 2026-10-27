"""Conditional event study: do strong-dragon signals work better when policy / industry heat is high?

The deck's 21 cases had company catalysts in only ~20% of the names; the rest started on industry or policy news.
The earlier selector study tested prices and a coarse policy proxy in a single five-month window. This study uses
the full 2019-2026 strong-dragon signal set (97,922 signals) and asks a cleaner question:

  For each signal, was its industry hot (price-based limit-up count / breadth / 5-day return), and was there
  positive policy news or a company catalyst before it? Then compare forward 1/3/5/10-day returns across those
  groups and across years.

Everything is point-in-time: industry heat and policy scores are shifted one trading day; announcements use the
publication date mapped to the next trading day. No future information enters the grouping.

usage:
  uv run python research/news_selector/policy_event_study.py --start 2019-01-01 --end 2026-10-26
Writes output/news_selector/policy_event_summary.json and policy_event_groups.csv.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "news_selector"))

from classify import classify_announcements_frame, classify_policy_frame  # noqa: E402
from quant.stock_data.news_store import NewsStore  # noqa: E402

OUT = ROOT / "output" / "news_selector"
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
INDEX = ROOT / "data" / "market" / "kline_index" / "000852.XSHG.csv"
if not INDEX.exists():
    INDEX = ROOT / "output" / "strong_dragon" / "kline_index" / "000852.XSHG.csv"
SIGNALS = ROOT / "output" / "strong_dragon" / "selector_trades.csv"
INDUSTRY = ROOT / "research" / "strong_dragon" / "industry_sw.csv"
HORIZONS = ("fwd1", "fwd3", "fwd5", "fwd10")


def limit_for(code: str, date: pd.Timestamp, is_st: bool) -> float:
    num = code.partition(".")[0]
    if is_st:
        return 0.05
    if num.startswith(("300", "301")):
        return 0.10 if date < pd.Timestamp("2020-08-24") else 0.20
    if num.startswith(("688", "689")):
        return 0.20
    if num.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def next_trade(dates: pd.DatetimeIndex, value) -> pd.Timestamp | None:
    d = pd.Timestamp(value)
    i = dates.searchsorted(d, side="left")
    return dates[i] if i < len(dates) else None


def load_signals(start: str, end: str) -> pd.DataFrame:
    tr = pd.read_csv(SIGNALS, usecols=["signal_date", "code", "status"])
    tr["signal_date"] = pd.to_datetime(tr["signal_date"])
    tr = tr[(tr["signal_date"] >= start) & (tr["signal_date"] <= end)]
    tr = tr[tr["status"] != "unfilled_limit_up_open"].drop_duplicates(["signal_date", "code"])
    print(f"[signals] {len(tr):,} tradable signals, {tr['code'].nunique():,} codes")
    return tr


def load_panel_and_signals(sig: pd.DataFrame, sw: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Industry daily aggregates for heat, and forward returns for the signal rows."""
    sig_by_code = {c: g["signal_date"].tolist() for c, g in sig.groupby("code", sort=False)}
    industry_parts, signal_parts = [], []
    for path in sorted(KLINE.glob("*.csv")):
        code = path.stem
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        if len(df) < 30:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        c = df["close"]
        lim = np.array([limit_for(code, d, bool(st)) for d, st in zip(df["date"], df["isST"].fillna(0))])
        limit_up = ((c / df["preclose"] - 1) >= (lim - 0.005)).astype(np.int8)
        ret5 = c / c.shift(5) - 1
        above = (c > c.rolling(20).mean()).astype(np.int8)
        sector = sw.get(code, "")
        industry_parts.append(pd.DataFrame({
            "date": df["date"], "sector": sector, "limit_up": limit_up,
            "ret5": ret5.astype("float32"), "above_ma20": above,
        }))
        if code in sig_by_code:
            entry = df["open"].shift(-1)
            fwd = pd.DataFrame({
                "date": df["date"],
                "fwd1": c.shift(-1) / entry - 1,
                "fwd3": c.shift(-3) / entry - 1,
                "fwd5": c.shift(-5) / entry - 1,
                "fwd10": c.shift(-10) / entry - 1,
            }).set_index("date")
            wanted = pd.DatetimeIndex(sig_by_code[code])
            sub = fwd.reindex(wanted).dropna(how="all")
            if len(sub):
                sub = sub.reset_index()
                sub = sub.rename(columns={sub.columns[0]: "signal_date"})
                sub["code"] = code
                signal_parts.append(sub)
    industry = pd.concat(industry_parts, ignore_index=True)
    fwd = pd.concat(signal_parts, ignore_index=True)
    print(f"[panel] industry rows {len(industry):,}; signal returns {len(fwd):,}")
    return industry, fwd


def industry_heat(industry: pd.DataFrame) -> pd.DataFrame:
    ind = (industry.groupby(["date", "sector"], observed=True)
           .agg(ind_limit=("limit_up", "sum"), ind_ret5=("ret5", "median"),
                ind_breadth=("above_ma20", "mean"), ind_n=("limit_up", "size"))
           .reset_index().sort_values(["sector", "date"]))
    for src, dst in (("ind_limit", "ind_limit_5"), ("ind_ret5", "ind_ret5_5"), ("ind_breadth", "ind_breadth_5")):
        ind[dst] = ind.groupby("sector")[src].transform(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    # Cross-sectional ranks by date, then a simple heat score.
    for c in ("ind_limit_5", "ind_ret5_5", "ind_breadth_5"):
        ind[c + "_rank"] = ind.groupby("date")[c].rank(pct=True)
    ind["ind_heat"] = (0.5 * ind["ind_limit_5_rank"] + 0.3 * ind["ind_ret5_5_rank"]
                       + 0.2 * ind["ind_breadth_5_rank"]).fillna(0)
    return ind


def policy_heat(store: NewsStore, ind: pd.DataFrame, trade_dates: pd.DatetimeIndex) -> pd.DataFrame:
    pol = store.policy_news(str(trade_dates[0].date()), str(trade_dates[-1].date()))
    if pol.empty:
        ind["policy_score_5"] = 0.0
        ind["policy_rank"] = 0.0
        return ind
    pol = classify_policy_frame(pol)
    pol = pol[pol["score"] > 0].copy()
    pol = pol.explode("themes").dropna(subset=["themes"])
    pol["sector"] = pol["themes"].map(lambda t: list(__import__("classify").THEME_TO_SW2.get(t, set())))
    pol = pol.explode("sector").dropna(subset=["sector"])
    pol["trade_date"] = [next_trade(trade_dates, d) for d in pol["date"]]
    pol = pol.dropna(subset=["trade_date"])
    daily = pol.groupby(["trade_date", "sector"])["score"].sum().reset_index().rename(
        columns={"trade_date": "date", "score": "policy_daily"})
    ind = ind.merge(daily, on=["date", "sector"], how="left")
    ind["policy_daily"] = ind["policy_daily"].fillna(0.0)
    ind = ind.sort_values(["sector", "date"])
    ind["policy_score_5"] = ind.groupby("sector")["policy_daily"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum())
    ind["policy_rank"] = ind.groupby("date")["policy_score_5"].rank(pct=True)
    print(f"[policy] {len(pol):,} theme rows, {ind['policy_score_5'].gt(0).sum():,} industry-days positive")
    return ind


def announcement_features(store: NewsStore, sig: pd.DataFrame, trade_dates: pd.DatetimeIndex) -> pd.DataFrame:
    ann = store.announcements(str(trade_dates[0].date()), str(trade_dates[-1].date()))
    if ann.empty:
        out = sig.copy()
        out["ann_pos_10"] = 0.0
        out["ann_neg_10"] = 0.0
        return out
    code_map = {c.partition(".")[0]: c for c in sig["code"].unique()}
    ann = classify_announcements_frame(ann)
    ann = ann[ann["score"] != 0].copy()
    ann["code"] = ann["code"].map(code_map)
    ann = ann.dropna(subset=["code"])
    ann["trade_date"] = [next_trade(trade_dates, d) for d in ann["date"]]
    ann = ann.dropna(subset=["trade_date"])
    daily = ann.groupby(["trade_date", "code"]).agg(
        ann_pos=("score", lambda s: float(s[s > 0].sum())),
        ann_neg=("score", lambda s: float(s[s < 0].min()) if (s < 0).any() else 0.0),
    ).reset_index()
    by_code = {c: g.set_index("trade_date") for c, g in daily.groupby("code", sort=False)}
    rows = []
    for code, g in sig.groupby("code", sort=False):
        d = by_code.get(code)
        if d is None:
            pos = pd.Series(0.0, index=g["signal_date"])
            neg = pd.Series(0.0, index=g["signal_date"])
        else:
            d = d[["ann_pos", "ann_neg"]]
            full = d.reindex(trade_dates).fillna(0.0)
            pos = full["ann_pos"].shift(1).rolling(10, min_periods=1).sum().reindex(g["signal_date"]).fillna(0.0)
            neg = full["ann_neg"].shift(1).rolling(10, min_periods=1).min().reindex(g["signal_date"]).fillna(0.0)
        part = g[["signal_date", "code"]].copy()
        part["ann_pos_10"] = pos.values
        part["ann_neg_10"] = neg.values
        rows.append(part)
    print(f"[ann] {len(ann):,} scored announcements, {daily['code'].nunique():,} codes")
    return pd.concat(rows, ignore_index=True)


def stats(df: pd.DataFrame, horizon: str) -> dict:
    x = df.dropna(subset=[horizon])
    if len(x) == 0:
        return {"n": 0}
    daily = x.groupby("signal_date")[horizon].mean()
    t = float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily)))) if len(daily) > 1 and daily.std() > 0 else None
    return {"n": int(len(x)), "mean": float(x[horizon].mean()), "median": float(x[horizon].median()),
            "win_rate": float((x[horizon] > 0).mean()), "daily_mean": float(daily.mean()), "t_stat": t}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2026-10-26")
    ap.add_argument("--db", default=str(ROOT / "data" / "news" / "news.db"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    store = NewsStore(args.db)
    print("[store]", store.counts())
    sw = dict(pd.read_csv(INDUSTRY, dtype={"code": str}).set_index("code")["sw1"])
    sig = load_signals(args.start, args.end)
    sig["sector"] = sig["code"].map(sw)
    industry, fwd = load_panel_and_signals(sig, sw)
    ind = industry_heat(industry)
    dates = pd.DatetimeIndex(pd.read_csv(INDEX, parse_dates=["date"])["date"]).sort_values()
    dates = dates[(dates >= args.start) & (dates <= args.end)]
    ind = policy_heat(store, ind, dates)
    ann = announcement_features(store, sig, dates)
    sig = sig.merge(fwd, on=["signal_date", "code"], how="left")
    sig = sig.merge(ind[["date", "sector", "ind_heat", "policy_score_5", "policy_rank"]],
                    left_on=["signal_date", "sector"], right_on=["date", "sector"], how="left")
    sig = sig.merge(ann, on=["signal_date", "code"], how="left")
    sig["ann_pos_10"] = sig["ann_pos_10"].fillna(0.0)
    sig["ann_neg_10"] = sig["ann_neg_10"].fillna(0.0)
    sig["ann_cat"] = np.where(sig["ann_neg_10"] < 0, "negative",
                              np.where(sig["ann_pos_10"] > 0, "positive", "none"))
    sig["ind_high"] = sig["ind_heat"] >= 0.8
    sig["ind_low"] = sig["ind_heat"] <= 0.2
    sig["policy_high"] = sig["policy_score_5"] > 0
    sig["policy_top"] = sig["policy_rank"] >= 0.8
    sig["policy_bottom"] = sig["policy_rank"] <= 0.2
    sig["year"] = sig["signal_date"].dt.year

    groups = {
        "all": pd.Series(True, index=sig.index),
        "industry_high": sig["ind_high"],
        "industry_low": sig["ind_low"],
        "policy_high": sig["policy_high"],
        "policy_zero": ~sig["policy_high"],
        "policy_top": sig["policy_top"],
        "policy_bottom": sig["policy_bottom"],
        "ann_positive": sig["ann_cat"] == "positive",
        "ann_negative": sig["ann_cat"] == "negative",
        "ann_none": sig["ann_cat"] == "none",
        "policy_high_industry_high": sig["policy_high"] & sig["ind_high"],
        "policy_top_industry_high": sig["policy_top"] & sig["ind_high"],
        "policy_top_ann_positive": sig["policy_top"] & (sig["ann_cat"] == "positive"),
        "policy_bottom_ann_positive": sig["policy_bottom"] & (sig["ann_cat"] == "positive"),
        "ann_positive_industry_high": (sig["ann_cat"] == "positive") & sig["ind_high"],
        "ann_positive_industry_low": (sig["ann_cat"] == "positive") & sig["ind_low"],
        "policy_high_ann_positive": sig["policy_high"] & (sig["ann_cat"] == "positive"),
        "policy_low_ann_positive": (~sig["policy_high"]) & (sig["ann_cat"] == "positive"),
    }
    summary = {"data": {"signals": int(len(sig)), "start": str(sig["signal_date"].min().date()),
                        "end": str(sig["signal_date"].max().date()),
                        "store": store.counts()},
               "groups": {}, "by_year": {}}
    rows = []
    for name, mask in groups.items():
        g = sig[mask]
        summary["groups"][name] = {h: stats(g, h) for h in HORIZONS}
        for h in HORIZONS:
            s = summary["groups"][name][h]
            rows.append({"group": name, "horizon": h, **s})
    for name in ("all", "industry_high", "policy_high", "policy_top", "ann_positive",
                 "policy_high_industry_high", "policy_top_industry_high", "ann_positive_industry_high",
                 "ann_positive_industry_low", "policy_low_ann_positive"):
        g = sig[groups[name]]
        summary["by_year"][name] = {str(int(y)): stats(x, "fwd5") for y, x in g.groupby("year")}
    pd.DataFrame(rows).to_csv(OUT / "policy_event_groups.csv", index=False)
    (OUT / "policy_event_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[groups] fwd5")
    for r in rows:
        if r["horizon"] == "fwd5":
            print(f"{r['group']:32s} n={r['n']:7d} mean={r.get('mean', 0):+.4f} "
                  f"median={r.get('median', 0):+.4f} win={r.get('win_rate', 0):.3f} t={r.get('t_stat') or 0:+.2f}")
    print("[done]", OUT / "policy_event_summary.json")


if __name__ == "__main__":
    main()
