"""Does "pick the right industry and its leader" (the deck team's manual step) rescue the signal?

Industry = Shenwan level-2 from industry_sw.csv (today's snapshot). All thresholds are fixed up front,
not tuned, and every variant is reported on two halves: train 2025-01..2025-08, test 2025-09..2026-03.

Variants (entry is always a fresh RED; execution and costs as in sd_signals.simulate):
  all            every fresh RED (baseline)
  hot            the stock's industry is in the top HOT_TOP industries by median 5-day return, and at least
                 MIN_SECTOR_RED of its members are RED that day (a sector move, not a lone stock)
  leader         the stock has the highest 5-day return among the RED members of its industry (>= MIN_SECTOR_RED)
  hot+leader     both
  regime         market filter: the previous signal cohort made money (3-day mean of yesterday's fresh-RED
                 stocks' open-to-close return > 0), i.e. chasing currently pays
  ...+exit_ma10  same entry, but exit when the close drops below MA10 instead of MA5 (let leaders run)
  hot pullback   pre-registered alternative: skip the signal day, buy the first pullback to MA5 within 5 bars
                 while MA5 > MA10 > MA20 holds and the industry is still hot

usage: uv run python research/strong_dragon/sector_study.py
Writes output/strong_dragon/sector_*.csv and sector_summary.json.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scan_universe import START_EQUITY, curve_stats, indicators, load, portfolio
from sd_data import HERE, OUT, daily_bars, limit_pct, load_cases
from sd_signals import COMMISSION, GREEN_LOOKBACK, SLIPPAGE, STAMP_DUTY, add_indicator, simulate

HOT_TOP = 5
MIN_SECTOR_RED = 2
MIN_INDUSTRY_SIZE = 5
STOP_LOSS = 0.08
SPLIT = pd.Timestamp("2025-09-01")


def add_features(ind: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    df = ind.merge(industry[["code", "sw2"]], on="code", how="left")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    g = df.groupby("code", sort=False)
    df["ret5"] = df["close"] / g["close"].shift(5) - 1
    df["oc_ret"] = df["close"] / df["open"] - 1
    recent_red = g["red"].transform(lambda s: s.astype(float).shift(1).rolling(GREEN_LOOKBACK, min_periods=1).max()).fillna(0) > 0
    df["exit_ma10"] = recent_red & ~df["red"] & (df["close"] < df["ma10"])

    sized = df.groupby("sw2")["code"].transform("nunique") >= MIN_INDUSTRY_SIZE
    day_ind = df[sized & df["sw2"].notna()].groupby(["date", "sw2"]).agg(
        ind_ret5=("ret5", "median"), ind_red=("red", "sum"))
    day_ind["ind_rank"] = day_ind.groupby(level=0)["ind_ret5"].rank(ascending=False, method="min")
    df = df.merge(day_ind.reset_index(), on=["date", "sw2"], how="left")
    red_ret5 = df["ret5"].where(df["red"])
    df["lead_rank"] = red_ret5.groupby([df["date"], df["sw2"]]).rank(ascending=False, method="min")

    # Cohort of day t = stocks with a fresh RED on t; its result (next day's open-to-close) is known on t+1.
    df["next_oc"] = g["oc_ret"].shift(-1)
    cohort = df[df["fresh_red"]].groupby("date")["next_oc"].mean()
    dates = pd.Index(sorted(df["date"].unique()))
    known_on = pd.Series(cohort.values, index=[dates[dates.get_loc(d) + 1] if dates.get_loc(d) + 1 < len(dates) else pd.NaT
                                               for d in cohort.index]).dropna()
    regime = known_on[known_on.index.notna()].reindex(dates).rolling(3, min_periods=1).mean() > 0
    df["regime_on"] = df["date"].map(regime).fillna(False).astype(bool)

    hot = (df["ind_rank"] <= HOT_TOP) & (df["ind_red"] >= MIN_SECTOR_RED)
    leader = (df["lead_rank"] == 1) & (df["ind_red"] >= MIN_SECTOR_RED)
    f = df["fresh_red"]
    df["e_all"] = f
    df["e_hot"] = f & hot
    df["e_leader"] = f & leader
    df["e_hot_leader"] = f & hot & leader
    df["e_regime"] = f & df["regime_on"]
    df["e_hot_leader_regime"] = f & hot & leader & df["regime_on"]

    # Pre-registered alternative (tested once, not tuned): do not chase the signal day; buy the first pullback
    # to MA5 within 5 bars after a fresh RED while the trend is intact and the industry is still hot.
    recent_fresh = g["fresh_red"].transform(lambda s: s.astype(float).shift(1).rolling(5, min_periods=1).max()).fillna(0) > 0
    trend = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])
    pull = recent_fresh & ~df["red"] & trend & (df["close"] <= df["ma5"] * 1.02) & (df["close"] > df["ma10"]) & hot
    df["e_hot_pullback"] = pull & ~(pull.groupby(df["code"]).shift(1, fill_value=False).astype(bool))
    return df


def fast_trades(df: pd.DataFrame, entry_col: str, exit_col: str = "green", stop_loss: float | None = STOP_LOSS) -> pd.DataFrame:
    """Same rules as sd_signals.simulate (one position per stock, next-open execution), on numpy arrays."""
    rows = []
    for code, g in df.groupby("code", sort=False):
        ent = g[entry_col].to_numpy()
        if not ent[:-1].any():
            continue
        o, c, h = g["open"].to_numpy(), g["close"].to_numpy(), g["high"].to_numpy()
        ex = g[exit_col].to_numpy()
        lim = g["lim"].fillna(limit_pct(code)).to_numpy()
        dates = g["date"].to_numpy()
        n, t = len(g), 0
        while t < n - 1:
            if not ent[t]:
                t += 1
                continue
            if o[t + 1] >= c[t] * (1 + lim[t + 1]) * 0.995:
                rows.append({"code": code, "signal_date": dates[t], "status": "unfilled_limit_up_open"})
                t += 1
                continue
            px = o[t + 1] * (1 + SLIPPAGE) * (1 + COMMISSION)
            peak, bars, k, done = h[t + 1], 0, t + 1, False
            while k < n - 1:
                bars += 1
                peak = max(peak, h[k])
                reason = exit_col if ex[k] else ("stop_loss" if stop_loss is not None and c[k] <= px * (1 - stop_loss) else None)
                if reason and o[k + 1] > c[k] * (1 - lim[k + 1]) * 1.005:
                    out = o[k + 1] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY)
                    rows.append({"code": code, "signal_date": dates[t], "entry_date": dates[t + 1], "exit_date": dates[k + 1],
                                 "status": "closed", "reason": reason, "ret": out / px - 1, "peak_ret": peak / px - 1, "bars": bars})
                    t, done = k + 1, True
                    break
                k += 1
            if not done:
                rows.append({"code": code, "signal_date": dates[t], "entry_date": dates[t + 1], "exit_date": dates[n - 1],
                             "status": "open_at_end", "reason": "end_of_data", "ret": c[n - 1] / px - 1,
                             "peak_ret": peak / px - 1, "bars": bars})
                break
    return pd.DataFrame(rows)


def stats(tr: pd.DataFrame) -> dict:
    if len(tr) == 0 or "ret" not in tr:
        return {"trades": 0}
    t = tr[tr["status"] != "unfilled_limit_up_open"]
    if len(t) == 0:
        return {"trades": 0}
    return {"trades": int(len(t)), "win_rate": round(float((t["ret"] > 0).mean()), 3),
            "mean_ret": round(float(t["ret"].mean()), 4), "median_ret": round(float(t["ret"].median()), 4),
            "share_ge_30pct": round(float((t["ret"] >= 0.30).mean()), 3), "mean_bars": round(float(t["bars"].mean()), 1)}


def case_check(df: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    """For the 2025 deck cases: on the case's first RED day in its claimed window, was it a hot-industry leader?"""
    cases = load_cases()
    cases = cases[cases["claimed_start"] >= pd.Timestamp("2025-01-01")]
    ind_map = industry.set_index("code")["sw2"]
    out = []
    for _, c in cases.iterrows():
        fs = (c["chart_start"] - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
        fe = (c["chart_end"] + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        s = add_indicator(daily_bars(c["code"], fs, fe, c["name"]))
        s["ret5"] = s["close"] / s["close"].shift(5) - 1
        win = s[(s["date"] >= c["claimed_start"] - pd.Timedelta(days=10)) & (s["date"] <= c["claimed_end"]) & s["red"]]
        if win.empty:
            continue
        d, r5 = win["date"].iat[0], win["ret5"].iat[0]
        sw2 = ind_map.get(c["code"])
        peers = df[(df["date"] == d) & (df["sw2"] == sw2) & (df["code"] != c["code"])]
        day = df[df["date"] == d]
        ranks = day.groupby("sw2")["ret5"].median().rank(ascending=False, method="min")
        peer_red = int(peers["red"].sum())
        stronger = int((peers["ret5"] > r5).sum())
        out.append({"case_id": c["case_id"], "name_en": c["name_en"], "first_red": d.date().isoformat(), "sw2": sw2,
                    "industry_rank": int(ranks.get(sw2, np.nan)) if sw2 in ranks else None, "industries": int(len(ranks)),
                    "peer_red_same_day": peer_red, "peers_in_universe": int(len(peers)),
                    "peers_stronger_5d": stronger, "is_industry_leader": stronger == 0})
    return pd.DataFrame(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    industry = pd.read_csv(HERE / "industry_sw.csv")
    px, bench = load()
    df = add_features(indicators(px), industry)
    print(f"universe {df['code'].nunique()} stocks; industries with >= {MIN_INDUSTRY_SIZE} members: "
          f"{df.loc[df.groupby('sw2')['code'].transform('nunique') >= MIN_INDUSTRY_SIZE, 'sw2'].nunique()}")

    # The fast simulator must reproduce sd_signals.simulate exactly on the baseline.
    sample = df[df["code"].isin(df["code"].drop_duplicates().head(150))]
    ref = pd.DataFrame([{"code": code, **t} for code, g in sample.groupby("code", sort=False)
                        for t in simulate(g, limit_pct(code), stop_loss=STOP_LOSS, entry="e_all")])
    fast = fast_trades(sample, "e_all")
    key = ["code", "signal_date", "status"]
    m = ref.assign(signal_date=pd.to_datetime(ref["signal_date"])).merge(fast, on=key, how="outer", suffixes=("_ref", "_fast"), indicator=True)
    agree = (m["_merge"] == "both").mean()
    ret_diff = (m["ret_ref"] - m["ret_fast"]).abs().max()
    print(f"fast simulator vs reference: {len(ref)} vs {len(fast)} trades, matched {agree:.1%}, max |ret diff| {ret_diff:.2e}")

    variants = [("all", "e_all", "green"), ("hot", "e_hot", "green"), ("leader", "e_leader", "green"),
                ("hot+leader", "e_hot_leader", "green"), ("regime", "e_regime", "green"),
                ("hot+leader+regime", "e_hot_leader_regime", "green"),
                ("hot+leader, exit_ma10", "e_hot_leader", "exit_ma10"),
                ("hot+leader+regime, exit_ma10", "e_hot_leader_regime", "exit_ma10"),
                ("hot pullback", "e_hot_pullback", "green"),
                # A pullback entry sits at or below MA5, so the MA5 exit fires at once; MA10 is the consistent exit.
                ("hot pullback, exit_ma10", "e_hot_pullback", "exit_ma10")]
    halves = {"train": df[df["date"] < SPLIT], "test": df[df["date"] >= SPLIT]}
    summary, rows = {"split": SPLIT.date().isoformat(), "hot_top": HOT_TOP, "min_sector_red": MIN_SECTOR_RED}, []
    for name, entry, exit_col in variants:
        tr = fast_trades(df, entry, exit_col)
        tr.to_csv(OUT / f"sector_trades_{name.replace('+', '_').replace(', ', '_')}.csv", index=False)
        sig = pd.to_datetime(tr["signal_date"]) if len(tr) else pd.Series(dtype="datetime64[ns]")
        res = {}
        for half, part in halves.items():
            ev = stats(tr[(sig < SPLIT) if half == "train" else (sig >= SPLIT)] if len(tr) else tr)
            curve, _ = portfolio(part, STOP_LOSS, 5e7, entry_col=entry, rank_col="ret5", exit_col=exit_col)
            pf = curve_stats(curve)
            res[half] = {"event": ev, "portfolio": {k: pf[k] for k in ("total_return", "max_drawdown")}}
            rows.append({"variant": name, "half": half, **ev,
                         "pf_total_return": round(pf["total_return"], 3), "pf_max_drawdown": round(pf["max_drawdown"], 3)})
        summary[name] = res
        print(name, json.dumps(res))
    for code, label in {"000852.XSHG": "CSI 1000", "399303.XSHE": "CNI 2000"}.items():
        b = bench[bench["code"] == code].set_index("date")["close"]
        summary[f"benchmark_{label}"] = {
            half: float(b[(b.index >= part["date"].min()) & (b.index <= part["date"].max())].iloc[[0, -1]].pct_change().iat[-1])
            for half, part in halves.items()}
    pd.DataFrame(rows).to_csv(OUT / "sector_variants.csv", index=False)
    cc = case_check(df, industry)
    cc.to_csv(OUT / "sector_case_check.csv", index=False)
    summary["case_check"] = cc.to_dict(orient="records")
    (OUT / "sector_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(pd.DataFrame(rows).to_string(index=False))
    print(cc.to_string(index=False))
    print("benchmarks:", {k: v for k, v in summary.items() if k.startswith("benchmark")})


if __name__ == "__main__":
    main()
