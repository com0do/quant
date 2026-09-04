"""Is the first limit-up of a brand-new theme (the "leader of a nascent theme") worth buying the next morning?

Data: Tonghuashun limit-up pools with the editors' limit-up reason (data/news/ths_limit_up/, archived by
scripts/news_archive.py; the site serves about one year back) and local prices (CSI 1000 + CSI 2000 databases).
Overlap: 2025-10 .. 2026-03. The reason text of day d is published on day d, so everything is known at the close.

Definitions, fixed before running:
  tags        the reason split on "+", e.g. "liquid cooling + special oil + Q3 growth" -> 3 tags
  new theme   a tag with at least MIN_IGNITION limit-ups on day d and none in the previous NEW_LOOKBACK trading days
  leader      the stock of a new theme with the earliest first seal time on day d; the others are followers
  hot theme   a tag seen in the previous NEW_LOOKBACK days with at least HOT_MIN limit-ups on day d
  lone        a limit-up whose tags no other limit-up stock shares that day
Trades: buy at the d+1 open (skipped when it opens at limit-up), sell at the close after 1 / 3 / 5 / 10 days, with the
same costs as the rest of the study. t-statistics use one mean per signal day, so busy days do not count many times.

usage: uv run python research/strong_dragon/theme_study.py
Writes output/strong_dragon/theme_events.csv, theme_groups.csv and theme_summary.json.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scan_universe import indicators, load
from sd_data import HERE, OUT, limit_pct
from sd_signals import COMMISSION, SLIPPAGE, STAMP_DUTY
from sector_study import STOP_LOSS, add_features, fast_trades
from catalyst_study import stats

THS_DIR = HERE.parents[1] / "data" / "news" / "ths_limit_up"
NEW_LOOKBACK = 20
MIN_IGNITION = 2
HOT_MIN = 3
HORIZONS = (1, 3, 5, 10)


def load_pool() -> pd.DataFrame:
    rows = []
    for p in sorted(THS_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                x = json.loads(line)
                num = str(x["code"]).zfill(6)
                suffix = ".XSHG" if num.startswith("6") else ".XSHE" if num.startswith(("0", "3")) else ".BJ"
                rows.append({"date": pd.Timestamp(x["date"]), "code": num + suffix, "name": x.get("name"),
                             "reason": x.get("reason_type") or "", "first_seal": int(x.get("first_limit_up_time") or 0),
                             "boards": x.get("high_days"), "open_num": x.get("open_num")})
    pool = pd.DataFrame(rows)
    pool["tags"] = pool["reason"].str.split("+").apply(lambda t: [s.strip() for s in t if s.strip()])
    return pool


def label_themes(pool: pd.DataFrame) -> pd.DataFrame:
    days = sorted(pool["date"].unique())
    tag_day = pool.explode("tags").dropna(subset=["tags"])
    counts = tag_day.groupby(["date", "tags"])["code"].nunique().unstack(fill_value=0).reindex(days, fill_value=0)
    seen_before = counts.shift(1).rolling(NEW_LOOKBACK, min_periods=1).sum().fillna(0) > 0
    new_tag = (counts >= MIN_IGNITION) & ~seen_before
    hot_tag = (counts >= HOT_MIN) & seen_before
    shared = counts >= 2

    def flags(r) -> pd.Series:
        d, tags = r["date"], r["tags"]
        return pd.Series({"new_tags": [t for t in tags if new_tag.at[d, t]], "hot": any(hot_tag.at[d, t] for t in tags),
                          "lone": not any(shared.at[d, t] for t in tags)})

    pool = pool.join(pool.apply(flags, axis=1))
    pool["new_theme"] = pool["new_tags"].str.len() > 0
    first_ix = pool[pool["new_theme"]].explode("new_tags").sort_values("first_seal").groupby(["date", "new_tags"])["code"].first()
    leaders = {(d, c) for (d, _), c in first_ix.items()}
    pool["leader"] = [(d, c) in leaders for d, c in zip(pool["date"], pool["code"])]
    pool["follower"] = pool["new_theme"] & ~pool["leader"]
    pool["first_board"] = pool["boards"] == "首板"  # Tonghuashun's label for a first limit-up
    pool["warmup"] = pool["date"] < days[min(NEW_LOOKBACK, len(days) - 1)]
    return pool


def forward_returns(px: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    px = px.sort_values(["code", "date"])
    out = []
    for code, g in px.groupby("code", sort=False):
        ev = events[events["code"] == code]
        if ev.empty:
            continue
        g = g.reset_index(drop=True)
        pos = {d: i for i, d in enumerate(g["date"])}
        o, c = g["open"].to_numpy(), g["close"].to_numpy()
        lim = g["lim"].fillna(limit_pct(code)).to_numpy()
        for idx, d in zip(ev.index, ev["date"]):
            i = pos.get(d)
            if i is None or i + 1 >= len(g):
                continue
            row = {"idx": idx, "buyable": o[i + 1] < c[i] * (1 + lim[i + 1]) * 0.995}
            entry = o[i + 1] * (1 + SLIPPAGE) * (1 + COMMISSION)
            for h in HORIZONS:
                k = i + h
                row[f"r{h}"] = (c[k] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY) / entry - 1) if k < len(g) else np.nan
            out.append(row)
    return pd.DataFrame(out).set_index("idx")


def group_stats(ev: pd.DataFrame, mask: pd.Series, name: str) -> dict:
    g = ev[mask]
    b = g[g["buyable"]]
    res = {"group": name, "events": int(len(g)), "buyable_share": round(float(g["buyable"].mean()), 3) if len(g) else None}
    for h in HORIZONS:
        r = b[f"r{h}"].dropna()
        daily = b.dropna(subset=[f"r{h}"]).groupby("date")[f"r{h}"].mean()
        se = daily.std(ddof=1) / np.sqrt(len(daily)) if len(daily) > 1 else np.nan
        res[f"mean_{h}d"] = round(float(r.mean()), 4) if len(r) else None
        res[f"win_{h}d"] = round(float((r > 0).mean()), 3) if len(r) else None
        res[f"t_days_{h}d"] = round(float(daily.mean() / se), 2) if se and se > 0 else None
    res["days"] = int(b["date"].nunique())
    return res


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pool = label_themes(load_pool())
    px, bench = load()
    px_end = px["date"].max()
    ev = pool[(~pool["warmup"]) & (pool["date"] < px_end) & pool["code"].isin(set(px["code"]))].copy()
    ev = ev.join(forward_returns(px, ev), how="inner")
    print(f"limit-up pool: {pool['date'].min().date()}..{pool['date'].max().date()}, {len(pool)} rows; "
          f"events with local prices: {len(ev)} on {ev['date'].nunique()} days ({ev['date'].min().date()}..{ev['date'].max().date()})")

    groups = [("all limit-ups", ev["code"].notna()), ("first board", ev["first_board"]),
              ("new-theme leader", ev["leader"]), ("new-theme follower", ev["follower"]),
              ("hot-theme member", ev["hot"] & ~ev["new_theme"]), ("lone limit-up", ev["lone"])]
    table = pd.DataFrame([group_stats(ev, m, n) for n, m in groups])

    b = bench[bench["code"] == "000852.XSHG"].set_index("date")["close"].sort_index()
    bench_rows = {}
    days = pd.Index(sorted(ev["date"].unique()))
    for h in HORIZONS:
        fwd = b.shift(-h) / b - 1
        bench_rows[f"mean_{h}d"] = round(float(fwd.reindex(days).mean()), 4)
    table = pd.concat([table, pd.DataFrame([{"group": "CSI 1000 (same days)", **bench_rows}])], ignore_index=True)

    industry = pd.read_csv(HERE / "industry_sw.csv")
    df = add_features(indicators(px), industry)
    df = df[(df["date"] >= ev["date"].min()) & (df["date"] <= ev["date"].max())].reset_index(drop=True)
    key = pool.set_index(["date", "code"])
    in_pool = df.set_index(["date", "code"]).index.isin(key.index)
    lead = df.set_index(["date", "code"]).index.isin(key[key["leader"]].index)
    sig = df["fresh_red"].to_numpy()
    red_rows = []
    for name, mask in {"fresh RED (same period)": sig, "fresh RED, limit-up that day": sig & in_pool,
                       "fresh RED, no limit-up that day": sig & ~in_pool, "fresh RED, new-theme leader": sig & lead}.items():
        df["e_tmp"] = mask
        red_rows.append({"variant": name, "signals": int(mask.sum()), **stats(fast_trades(df, "e_tmp", stop_loss=STOP_LOSS))})
    red = pd.DataFrame(red_rows)

    ev_out = ev.drop(columns=["tags", "new_tags"]).assign(new_tags=ev["new_tags"].str.join("+"))
    ev_out.to_csv(OUT / "theme_events.csv", index=False)
    table.to_csv(OUT / "theme_groups.csv", index=False)
    summary = {"new_lookback": NEW_LOOKBACK, "min_ignition": MIN_IGNITION, "hot_min": HOT_MIN,
               "period": [str(ev["date"].min().date()), str(ev["date"].max().date())], "groups": table.to_dict(orient="records"),
               "red_signals": red.to_dict(orient="records")}
    cases = {"603778.XSHG": "Guosheng Technology (c20)"}
    summary["cases"] = {label: pool[(pool["code"] == code) & (pool["date"].between("2025-10-25", "2025-11-20"))]
                        [["date", "reason", "boards", "new_tags", "leader", "hot", "lone"]].astype(str).to_dict(orient="records")
                        for code, label in cases.items()}
    (OUT / "theme_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    pd.set_option("display.width", 250)
    print(table.to_string(index=False))
    print(red.to_string(index=False))
    print(json.dumps(summary["cases"], ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    main()
