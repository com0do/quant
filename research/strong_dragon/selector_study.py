"""Selector study for the strong-dragon signal: which signals, if any, beat the index out of sample?

The indicator is the approximation from `sd_signals.py` (MA5 > MA10 > MA20 and 3-day return >= 10%). A "fresh red"
signal is the first such day after a non-red day. The study asks the deck's missing question: of the hundreds of
signals every month, which ones does the team pick, and does that selection add value?

Design (fixed before looking at the test period):
  train 2019-01-01 .. 2022-12-31
  validation 2023-01-01 .. 2024-12-31
  test 2025-01-01 .. 2026-10-26

Every signal is a trade: decide at the close of day t, buy at the open of t+1 (skip a limit-up open), sell at the
open after a green bar, an 8% stop or the end of the data. Costs: 0.03% commission, 0.05% stamp duty on sells,
0.1% slippage. The event study treats each signal independently; the portfolio follows the deck's 30%-per-name,
at-most-3-names sizing and ranks signals with the selector being tested.

Selectors are a small, economically motivated set, not a grid: momentum, volume expansion, relative strength,
first limit-up, industry heat/leadership, a composite "dragon score", a market-regime filter, and a gradient-boosted
classifier trained on the train period and early-tested on validation. A random selector with the same trade count
is the placebo.

usage:
  uv run python research/strong_dragon/selector_study.py --all
Writes output/strong_dragon/selector_summary.json and prints the tables used in the README.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from sd_data import HERE, OUT
from sd_signals import COMMISSION, EPS, GREEN_LOOKBACK, RED_RET3, SLIPPAGE, STAMP_DUTY

START_EQUITY = 1_000_000.0
MAX_POS = 3
POS_PCT = 0.30
STOP_LOSS = 0.08
MIN_MONEY = 5e7
TRAIN_END = pd.Timestamp("2022-12-31")
VAL_END = pd.Timestamp("2024-12-31")
PERIODS = {"train": (pd.Timestamp("2019-01-01"), TRAIN_END),
           "val": (TRAIN_END + pd.Timedelta(days=1), VAL_END),
           "test": (VAL_END + pd.Timedelta(days=1), pd.Timestamp("2026-12-31"))}
FEATURES = [
    "ret3", "ret5", "ret10", "ret20", "ret60", "vol_ratio5", "vol_ratio20", "amount20", "turn20", "vol20",
    "dist_high20", "dist_high60", "dist_high250", "limit_up_count5", "limit_up_count20", "first_limit_up",
    "ma20_slope", "close_ma20", "rel_ret5", "ind_ret5", "ind_ret20", "ind_rank5", "ind_fresh_red",
    "ind_limit_up", "market_ret5", "market_ret20", "market_above_ma20", "breadth", "market_limit_up",
]


def load_prices(limit: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str], dict[str, pd.Series]]:
    files = sorted((OUT / "kline").glob("*.csv"))
    if limit:
        files = files[:limit]
    if not files:
        raise SystemExit(f"no bars in {OUT / 'kline'}; run fetch_universe.py first")
    frames = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["date"])
        for c in ("open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg", "isST"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df["code"] = f.stem
        frames.append(df)
    px = pd.concat(frames, ignore_index=True)
    px = px.dropna(subset=["close", "preclose"]).sort_values(["code", "date"]).reset_index(drop=True)
    ind = pd.read_csv(HERE / "industry_sw.csv", dtype={"code": "string"})
    sw = dict(zip(ind["code"], ind["sw2"]))
    bench = {}
    for code, name in (("000852.XSHG", "CSI 1000"), ("399303.XSHE", "CNI 2000")):
        f = OUT / "kline_index" / f"{code}.csv"
        if f.exists():
            b = pd.read_csv(f, parse_dates=["date"]).set_index("date")["close"].sort_index()
            bench[name] = b
    return px, ind, sw, bench


def stock_features(g: pd.DataFrame, sw2: str) -> pd.DataFrame:
    g = g.sort_values("date").reset_index(drop=True)
    c = g["close"]
    for n in (5, 10, 20, 60):
        g[f"ma{n}"] = c.rolling(n).mean()
    for n in (3, 5, 10, 20, 60):
        g[f"ret{n}"] = c / c.shift(n) - 1
    num = g["code"].iloc[0].partition(".")[0]
    if num.startswith(("300", "301")):
        base = pd.Series(0.20, index=g.index).where(g["date"] >= pd.Timestamp("2020-08-24"), 0.10)
    elif num.startswith(("688", "689")):
        base = pd.Series(0.20, index=g.index)
    elif num.startswith(("8", "4", "920")):
        base = pd.Series(0.30, index=g.index)
    else:
        base = pd.Series(0.10, index=g.index)
    g["lim"] = base.where(g["isST"].fillna(0).astype(int) == 0, 0.05)
    g["limit_up"] = (c / g["preclose"] - 1) >= (g["lim"] - 0.005)
    g["red"] = ((g["ma5"] > g["ma10"] * (1 + EPS)) & (g["ma10"] > g["ma20"] * (1 + EPS))
                & (g["ret3"] >= RED_RET3 - EPS))
    recent_red = g["red"].astype(float).shift(1).rolling(GREEN_LOOKBACK, min_periods=1).max().fillna(0) > 0
    g["green"] = recent_red & ~g["red"] & (c < g["ma5"] * (1 - EPS))
    g["fresh_red"] = g["red"] & ~g["red"].shift(1, fill_value=False).astype(bool)
    g["vol_ratio5"] = g["volume"] / g["volume"].rolling(5).mean()
    g["vol_ratio20"] = g["volume"] / g["volume"].rolling(20).mean()
    g["amount20"] = g["amount"].rolling(20).mean()
    g["turn20"] = g["turn"].rolling(20).mean()
    g["vol20"] = (g["pctChg"] / 100).rolling(20).std()
    g["dist_high20"] = c / g["high"].rolling(20).max() - 1
    g["dist_high60"] = c / g["high"].rolling(60).max() - 1
    g["dist_high250"] = c / g["high"].rolling(250).max() - 1
    g["limit_up_count5"] = g["limit_up"].astype(float).rolling(5).sum()
    g["limit_up_count20"] = g["limit_up"].astype(float).rolling(20).sum()
    g["first_limit_up"] = g["limit_up"] & (g["limit_up"].astype(float).shift(1).rolling(20, min_periods=1).max() == 0)
    g["ma20_slope"] = g["ma20"] / g["ma20"].shift(5) - 1
    g["close_ma20"] = c / g["ma20"] - 1
    g["above_ma20"] = (c > g["ma20"]).astype(float)
    g["sw2"] = sw2
    return g


def build(limit: int = 0):
    px, ind, sw, bench = load_prices(limit)
    signals, panels = [], []
    for code, g in px.groupby("code", sort=False):
        f = stock_features(g, sw.get(code, ""))
        signals.append(f[f["fresh_red"]])
        panels.append(f[["date", "code", "open", "high", "low", "close", "preclose", "lim", "green",
                         "fresh_red", "ret3", "ret5", "amount20", "above_ma20", "limit_up", "sw2"]])
    sig = pd.concat(signals, ignore_index=True)
    panel = pd.concat(panels, ignore_index=True)
    print(f"[data] {px['code'].nunique()} stocks, {len(px):,} rows, {len(sig):,} fresh-red signals, "
          f"{sig['date'].min().date()} .. {sig['date'].max().date()}")

    # Cross-sectional: industry heat, breadth and market-wide limit-ups come from the whole panel.
    ind_frame = (panel.groupby(["date", "sw2"], observed=True)
                 .agg(ind_ret5=("ret5", "median"), ind_fresh_red=("fresh_red", "sum"),
                      ind_limit_up=("limit_up", "sum"), ind_n=("code", "size")).reset_index())
    ind_frame["ind_rank5"] = ind_frame.groupby("date")["ind_ret5"].rank(pct=True)
    market = (panel.groupby("date").agg(breadth=("above_ma20", "mean"),
                                         market_limit_up=("limit_up", "sum")).reset_index())
    sig = sig.merge(ind_frame, on=["date", "sw2"], how="left").merge(market, on="date", how="left")
    sig["rel_ret5"] = sig["ret5"] - sig["ind_ret5"]
    sig = sig.sort_values(["date", "sw2", "ret5"], ascending=[True, True, False])
    sig["ind_rank_in"] = sig.groupby(["date", "sw2"]).cumcount() + 1
    sig["ind_leader"] = ((sig["ind_rank_in"] == 1) & (sig["ind_fresh_red"] >= 2)).astype(float)

    if bench:
        b = pd.DataFrame(bench)
        b["market_ret5"] = b["CSI 1000"].pct_change(5)
        b["market_ret20"] = b["CSI 1000"].pct_change(20)
        b["market_above_ma20"] = (b["CSI 1000"] > b["CSI 1000"].rolling(20).mean()).astype(float)
        sig = sig.merge(b[["market_ret5", "market_ret20", "market_above_ma20"]].reset_index(),
                        on="date", how="left")

    sig["dragon_score"] = (z(sig, "ret3") + z(sig, "vol_ratio20") + z(sig, "rel_ret5")
                           + z(sig, "limit_up_count5") - z(sig, "vol20"))
    sig["hot_industry_score"] = sig["ind_rank5"].fillna(0.5) + sig["ind_leader"] + z(sig, "rel_ret5")
    sig["regime_ok"] = (sig["market_above_ma20"].fillna(0) > 0) & (sig["breadth"].fillna(0) > 0.5)
    return sig, panel, bench


def z(frame: pd.DataFrame, col: str) -> pd.Series:
    s = frame[col].astype(float)
    grp = s.groupby(frame["date"])
    return ((s - grp.transform("mean")) / (grp.transform("std") + 1e-12)).fillna(0)


def simulate_trades(panel: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code, g in panel.groupby("code", sort=False):
        lim = g["lim"].iloc[0] if len(g) else 0.10
        pos = None
        g = g.reset_index(drop=True)
        for t in range(len(g) - 1):
            today, nxt = g.iloc[t], g.iloc[t + 1]
            nlim = nxt["lim"] if pd.notna(nxt["lim"]) else lim
            if pos is None:
                if bool(today["fresh_red"]):
                    if nxt["open"] >= nxt["preclose"] * (1 + nlim) * 0.995:
                        rows.append({"code": code, "signal_date": today["date"],
                                     "status": "unfilled_limit_up_open"})
                        continue
                    pos = {"code": code, "signal_date": today["date"], "entry_date": nxt["date"],
                           "entry_px": nxt["open"] * (1 + SLIPPAGE) * (1 + COMMISSION),
                           "bars": 0, "peak": nxt["high"]}
                continue
            pos["bars"] += 1
            pos["peak"] = max(pos["peak"], today["high"])
            reason = None
            if bool(today["green"]):
                reason = "green"
            elif today["close"] <= pos["entry_px"] * (1 - STOP_LOSS):
                reason = "stop_loss"
            if reason and nxt["open"] > nxt["preclose"] * (1 - nlim) * 1.005:
                exit_px = nxt["open"] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY)
                rows.append({**pos, "status": "closed", "exit_date": nxt["date"], "exit_px": exit_px,
                             "reason": reason, "ret": exit_px / pos["entry_px"] - 1,
                             "peak_ret": pos["peak"] / pos["entry_px"] - 1})
                pos = None
        if pos is not None:
            last = g.iloc[-1]
            rows.append({**pos, "status": "open_at_end", "exit_date": last["date"], "exit_px": last["close"],
                         "reason": "end_of_data", "ret": last["close"] / pos["entry_px"] - 1,
                         "peak_ret": pos["peak"] / pos["entry_px"] - 1})
    tr = pd.DataFrame(rows)
    sig_cols = ["date", "code"] + FEATURES + ["ind_leader", "dragon_score", "hot_industry_score", "regime_ok"]
    sig_cols = [c for c in dict.fromkeys(sig_cols) if c in sig.columns]
    tr = tr.merge(sig[sig_cols], left_on=["signal_date", "code"], right_on=["date", "code"], how="left")
    tr = tr.drop(columns=["date"], errors="ignore")
    return tr


def stats(tr: pd.DataFrame) -> dict:
    t = tr[tr["status"].eq("closed") & tr["ret"].notna()]
    out = {"trades": int(len(t)),
           "unfilled": int(tr["status"].eq("unfilled_limit_up_open").sum())}
    if not len(t):
        return out
    daily = t.groupby("signal_date")["ret"].mean()
    out.update({
        "win_rate": float((t["ret"] > 0).mean()), "mean_ret": float(t["ret"].mean()),
        "median_ret": float(t["ret"].median()), "p10_ret": float(t["ret"].quantile(0.10)),
        "p90_ret": float(t["ret"].quantile(0.90)), "share_ge_30pct": float((t["ret"] >= 0.30).mean()),
        "mean_bars": float(t["bars"].mean()),
        "t_stat": float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily)))) if len(daily) > 1 and daily.std() > 0 else None,
    })
    return out


def selected(sig: pd.DataFrame, score: str, n: int, mask: pd.Series | None = None) -> pd.DataFrame:
    s = sig if mask is None else sig[mask]
    s = s[s["amount20"].fillna(0) >= MIN_MONEY]
    s = s.dropna(subset=[score]).sort_values(["date", score], ascending=[True, False])
    rank = s.groupby("date").cumcount() + 1
    return s[rank <= n]


def curve_stats(curve: pd.DataFrame) -> dict:
    eq = curve.set_index("date")["equity"]
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "total_return": float(eq.iat[-1] / eq.iat[0] - 1),
            "cagr": float((eq.iat[-1] / eq.iat[0]) ** (1 / years) - 1) if years > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * math.sqrt(244)) if r.std() > 0 else None}


def portfolio(panel: pd.DataFrame, sig: pd.DataFrame, score: str, mask: pd.Series | None = None,
              start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
              by_code: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = panel if start is None else panel[(panel["date"] >= start) & (panel["date"] <= end)]
    dates = sorted(p["date"].unique())
    if by_code is None:
        by_code = {c: g.set_index("date") for c, g in p.groupby("code", sort=False)}
    s = sig if mask is None else sig[mask]
    if "amount20" in s.columns:
        s = s[s["amount20"].fillna(0) >= MIN_MONEY]
    s = s.dropna(subset=[score]).sort_values([score], ascending=False)
    by_date = {d: list(zip(g["code"], g[score])) for d, g in s.groupby("date")}
    cash, hold, last_px = START_EQUITY, {}, {}
    pend_sell, pend_buy, curve, closed = set(), [], [], []
    for d in dates:
        for code in list(pend_sell):
            g = by_code.get(code)
            if g is None or d not in g.index:
                continue
            r = g.loc[d]
            lim = r["lim"] if pd.notna(r["lim"]) else 0.10
            if r["open"] <= r["preclose"] * (1 - lim) * 1.005:
                continue
            px = r["open"] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY)
            cash += hold[code]["qty"] * px
            closed.append({"code": code, "entry_date": hold[code]["date"], "exit_date": d,
                           "reason": hold[code]["reason"], "ret": px / hold[code]["px"] - 1,
                           "bars": hold[code]["bars"]})
            del hold[code]
            pend_sell.discard(code)
        equity_prev = cash + sum(h["qty"] * last_px.get(c, h["px"]) for c, h in hold.items())
        for code, _ in pend_buy:
            if len(hold) >= MAX_POS or code in hold:
                continue
            g = by_code.get(code)
            if g is None or d not in g.index:
                continue
            r = g.loc[d]
            lim = r["lim"] if pd.notna(r["lim"]) else 0.10
            if r["open"] >= r["preclose"] * (1 + lim) * 0.995:
                continue
            px = r["open"] * (1 + SLIPPAGE) * (1 + COMMISSION)
            qty = math.floor(min(equity_prev * POS_PCT, cash) / px / 100) * 100
            if qty <= 0:
                continue
            cash -= qty * px
            hold[code] = {"qty": qty, "px": px, "date": d, "bars": 0, "reason": ""}
            last_px[code] = px
        pend_buy = []
        for code, h in hold.items():
            g = by_code.get(code)
            if g is None or d not in g.index:
                continue
            r = g.loc[d]
            last_px[code] = r["close"]
            h["bars"] += 1
            if bool(r["green"]):
                h["reason"] = "green"
                pend_sell.add(code)
            elif r["close"] <= h["px"] * (1 - STOP_LOSS):
                h["reason"] = "stop_loss"
                pend_sell.add(code)
        equity = cash + sum(h["qty"] * last_px.get(c, h["px"]) for c, h in hold.items())
        curve.append({"date": d, "equity": equity, "n_hold": len(hold)})
        cand = [(c, sc) for c, sc in by_date.get(d, []) if c not in hold]
        pend_buy = cand[:MAX_POS]
    return pd.DataFrame(curve), pd.DataFrame(closed)


def benchmark_curves(panel: pd.DataFrame, bench: dict, dates: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame({"date": dates})
    ret = (panel["close"] / panel["preclose"] - 1).clip(-0.5, 0.5)
    ew = panel.assign(ret=ret).groupby("date")["ret"].mean()
    out["Equal-weight A"] = START_EQUITY * (1 + ew.reindex(dates).fillna(0)).cumprod().values
    for name, s in bench.items():
        out[name] = START_EQUITY * s.reindex(dates).ffill().values / s.dropna().iloc[0]
    return out


def ml_study(sig: pd.DataFrame, tr: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    data = sig.merge(tr[["signal_date", "code", "ret", "status"]],
                     left_on=["date", "code"], right_on=["signal_date", "code"], how="left")
    data = data.drop(columns=["signal_date", "code_y"], errors="ignore").rename(columns={"code_x": "code"})
    data = data.sort_values("date").reset_index(drop=True)
    feats = [c for c in FEATURES if c in data.columns]
    closed = data[data["status"].eq("closed") & data["ret"].notna()].copy()
    train = closed[closed["date"] <= TRAIN_END]
    val = closed[(closed["date"] > TRAIN_END) & (closed["date"] <= VAL_END)]
    info = {"features": feats, "train_rows": int(len(train)), "val_rows": int(len(val))}
    if len(train) < 1000 or len(val) < 200:
        return data, {**info, "error": "not enough labelled signals"}
    model = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15,
                                           l2_regularization=1.0, random_state=42, early_stopping=True,
                                           validation_fraction=0.15)
    model.fit(train[feats], (train["ret"] > 0).astype(int))
    info["val_auc"] = float(roc_auc_score((val["ret"] > 0).astype(int), model.predict_proba(val[feats])[:, 1]))
    data["ml_score"] = np.nan
    data.loc[val.index, "ml_score"] = model.predict_proba(val[feats])[:, 1]
    full = closed[closed["date"] <= VAL_END]
    final = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15,
                                           l2_regularization=1.0, random_state=42, early_stopping=True,
                                           validation_fraction=0.15)
    final.fit(full[feats], (full["ret"] > 0).astype(int))
    test = data[(data["date"] > VAL_END) & data["status"].eq("closed") & data["ret"].notna()]
    if len(test) > 100:
        data.loc[test.index, "ml_score"] = final.predict_proba(test[feats])[:, 1]
        info["test_auc"] = float(roc_auc_score((test["ret"] > 0).astype(int), data.loc[test.index, "ml_score"]))
    sample = val.sample(min(2000, len(val)), random_state=42)
    imp = permutation_importance(model, sample[feats], (sample["ret"] > 0).astype(int),
                                 n_repeats=3, random_state=42, scoring="roc_auc")
    info["permutation_importance"] = {f: float(v) for f, v in
                                      sorted(zip(feats, imp.importances_mean), key=lambda x: -x[1])[:12]}
    return data, info


def summarise(name: str, sel: pd.DataFrame, tr: pd.DataFrame, panel: pd.DataFrame,
              bench: dict, full_dates: pd.DatetimeIndex) -> dict:
    out = {}
    for period, (a, b) in PERIODS.items():
        t = tr[(tr["signal_date"] >= a) & (tr["signal_date"] <= b)]
        out[period] = {"event": stats(t)}
        p, closed = portfolio(panel, sel, "score", start=a, end=b, by_code=by_code)
        if len(p) > 2:
            out[period]["portfolio"] = {**curve_stats(p), **stats(
                closed.assign(status="closed", signal_date=closed["entry_date"]))}
        else:
            out[period]["portfolio"] = {}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="debug: only the first N stocks")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sig, panel, bench = build(a.limit)
    tr = simulate_trades(panel, sig)
    tr.to_csv(OUT / "selector_trades.csv", index=False)
    print(f"[trades] {len(tr):,} signals, {tr['status'].eq('closed').sum():,} closed, "
          f"{tr['status'].eq('unfilled_limit_up_open').sum():,} unfilled")

    summary = {"data": {"stocks": int(panel["code"].nunique()), "signals": int(len(sig)),
                        "start": str(panel["date"].min().date()), "end": str(panel["date"].max().date())},
               "baseline": {}, "selectors": {}, "ml": {}}
    by_code = {c: g.set_index("date") for c, g in panel.groupby("code", sort=False)}
    for period, (x, y) in PERIODS.items():
        t = tr[(tr["signal_date"] >= x) & (tr["signal_date"] <= y)]
        summary["baseline"][period] = stats(t)
        if len(t):
            summary["baseline"][period]["by_year"] = {
                str(int(k)): stats(g) for k, g in t.groupby(t["signal_date"].dt.year)}
    print("[baseline]", json.dumps(summary["baseline"], ensure_ascii=False, indent=1))

    selectors = {
        "ret3": "ret3",
        "ret5": "ret5",
        "vol_ratio20": "vol_ratio20",
        "rel_ret5": "rel_ret5",
        "first_limit_up": "first_limit_up",
        "dragon_score": "dragon_score",
        "hot_industry": "hot_industry_score",
    }
    for name, col in selectors.items():
        sig[name] = sig[col].astype(float)
        for n in (1, 3):
            sel = selected(sig, name, n)
            t = tr.merge(sel[["date", "code", name]], left_on=["signal_date", "code"],
                         right_on=["date", "code"], how="inner")
            summary["selectors"][f"{name}_top{n}"] = {
                p: stats(t[(t["signal_date"] >= x) & (t["signal_date"] <= y)]) for p, (x, y) in PERIODS.items()}
        psig = selected(sig, name, 3)[["date", "code", name]].rename(columns={name: "score"})
        summary["selectors"][f"{name}_portfolio"] = {}
        for p, (x, y) in PERIODS.items():
            curve, closed = portfolio(panel, psig, "score", start=x, end=y, by_code=by_code)
            summary["selectors"][f"{name}_portfolio"][p] = (
                {**curve_stats(curve), **stats(closed.assign(status="closed", signal_date=closed["entry_date"]))}
                if len(curve) > 2 else {})
        print(f"[selector] {name}: top1 train/val/test mean =",
              [round(summary["selectors"][f"{name}_top1"][p].get("mean_ret", float("nan")), 4)
               for p in PERIODS])

    base_sig = sig[["date", "code", "ret3", "amount20"]].rename(columns={"ret3": "score"})
    summary["baseline_portfolio"] = {}
    for p, (x, y) in PERIODS.items():
        curve, closed = portfolio(panel, base_sig, "score", start=x, end=y, by_code=by_code)
        summary["baseline_portfolio"][p] = (
            {**curve_stats(curve), **stats(closed.assign(status="closed", signal_date=closed["entry_date"]))}
            if len(curve) > 2 else {})

    regime = sig[sig["regime_ok"]]
    summary["selectors"]["regime_dragon_top1"] = {
        p: stats(tr.merge(selected(regime, "dragon_score", 1)[["date", "code", "dragon_score"]],
                          left_on=["signal_date", "code"], right_on=["date", "code"], how="inner")
                 .query("signal_date >= @x and signal_date <= @y")) for p, (x, y) in PERIODS.items()}
    regime_sig = selected(regime, "dragon_score", 3)[["date", "code", "dragon_score", "amount20"]] \
        .rename(columns={"dragon_score": "score"})
    summary["selectors"]["regime_dragon_portfolio"] = {}
    for p, (x, y) in PERIODS.items():
        curve, closed = portfolio(panel, regime_sig, "score", start=x, end=y, by_code=by_code)
        summary["selectors"]["regime_dragon_portfolio"][p] = (
            {**curve_stats(curve), **stats(closed.assign(status="closed", signal_date=closed["entry_date"]))}
            if len(curve) > 2 else {})

    data, ml_info = ml_study(sig, tr)
    summary["ml"] = ml_info
    if "ml_score" in data:
        md = data.dropna(subset=["ml_score"]).copy()
        md = md[md["amount20"].fillna(0) >= MIN_MONEY]
        md = md.sort_values(["date", "ml_score"], ascending=[True, False])
        md["rank"] = md.groupby("date").cumcount() + 1
        top1 = md[md["rank"] == 1]
        t = tr.merge(top1[["date", "code", "ml_score"]], left_on=["signal_date", "code"],
                     right_on=["date", "code"], how="inner")
        summary["selectors"]["ml_top1"] = {
            p: stats(t[(t["signal_date"] >= x) & (t["signal_date"] <= y)]) for p, (x, y) in PERIODS.items()}
        psig = md[["date", "code", "ml_score", "amount20"]].rename(columns={"ml_score": "score"})
        summary["selectors"]["ml_portfolio"] = {}
        for p in ("val", "test"):
            x, y = PERIODS[p]
            curve, closed = portfolio(panel, psig, "score", start=x, end=y, by_code=by_code)
            summary["selectors"]["ml_portfolio"][p] = (
                {**curve_stats(curve), **stats(closed.assign(status="closed", signal_date=closed["entry_date"]))}
                if len(curve) > 2 else {})
    print("[ml]", json.dumps(ml_info, ensure_ascii=False, indent=1)[:1500])

    # Benchmarks over the full sample and the test period.
    dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    eq = benchmark_curves(panel, bench, dates)
    summary["benchmarks"] = {}
    for p, (x, y) in PERIODS.items():
        q = eq[(eq["date"] >= x) & (eq["date"] <= y)]
        if len(q) > 2:
            summary["benchmarks"][p] = {c: curve_stats(q[["date", c]].rename(columns={c: "equity"}))
                                        for c in q.columns if c != "date"}
    (OUT / "selector_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[done] wrote {OUT / 'selector_summary.json'}")


if __name__ == "__main__":
    main()
