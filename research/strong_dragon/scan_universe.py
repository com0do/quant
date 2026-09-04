"""Run the approximated signal over the whole local small-cap universe (no cherry-picking).

Universe: every stock in data/runtime/market_csi1000_latest.db and market_csi2000_latest.db
(about 3,000 CSI 1000 / CSI 2000 names, 2024-12-24 .. 2026-03-13). Prices there are unadjusted, so an
adjusted series is rebuilt from the exchange pre_close (close / pre_close - 1 is the true daily return).

Two measurements:
1. Event study: every fresh RED signal is one independent trade (next-open entry, GREEN or stop-loss exit).
2. Portfolio: the deck's sizing (30% of equity per stock, at most 3 holdings), signals ranked by 3-day return.

usage: uv run python research/strong_dragon/scan_universe.py
Writes output/strong_dragon/universe_*.csv and universe_summary.json.
"""
from __future__ import annotations

import json
import math
import sqlite3

import numpy as np
import pandas as pd

from sd_data import OUT, REPO, limit_pct
from sd_signals import COMMISSION, SLIPPAGE, STAMP_DUTY, add_indicator, simulate

DBS = [REPO / "data" / "runtime" / "market_csi1000_latest.db", REPO / "data" / "runtime" / "market_csi2000_latest.db"]
BENCH = {"000852.XSHG": "CSI 1000", "399303.XSHE": "CNI 2000"}
START_EQUITY = 1_000_000.0


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    px, idx = [], []
    for db in DBS:
        con = sqlite3.connect(db)
        px.append(pd.read_sql("select date, code, open, high, low, close, volume, money, high_limit, pre_close "
                              "from prices_daily", con))
        idx.append(pd.read_sql("select date, code, close from index_prices_daily", con))
        con.close()
    df = pd.concat(px).drop_duplicates(["date", "code"]).sort_values(["code", "date"]).reset_index(drop=True)
    # Suspended days are stored with the previous close and empty open/volume; drop them (= skip_paused).
    df = df[df["open"].notna() & df["volume"].notna()].reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    ret = (df["close"] / df["pre_close"] - 1).where(df["pre_close"] > 0).fillna(0.0)
    df["adj"] = (1 + ret).groupby(df["code"]).cumprod()
    f = df["adj"] / df["close"]
    for c in ("open", "high", "low"):
        df[c] = df[c] * f
    df["close"] = df["adj"]
    lim = (df["high_limit"] / df["pre_close"] - 1).round(2)
    df["lim"] = lim.where(lim.isin([0.05, 0.1, 0.2, 0.3]))
    df["money20"] = df.groupby("code")["money"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    bench = pd.concat(idx).drop_duplicates(["date", "code"])
    bench["date"] = pd.to_datetime(bench["date"])
    return df.drop(columns=["adj", "high_limit", "pre_close"]), bench


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for code, g in df.groupby("code", sort=False):
        parts.append(add_indicator(g))
    return pd.concat(parts, ignore_index=True)


def event_study(ind: pd.DataFrame, stop_loss: float | None) -> pd.DataFrame:
    rows = []
    for code, g in ind.groupby("code", sort=False):
        for t in simulate(g, limit_pct(code), stop_loss=stop_loss):
            rows.append({"code": code, **t})
    tr = pd.DataFrame(rows)
    if not tr.empty:
        for c in ("signal_date", "entry_date", "exit_date"):
            if c in tr:
                tr[c] = pd.to_datetime(tr[c])
    return tr


def portfolio(ind: pd.DataFrame, stop_loss: float | None, min_money: float, max_pos: int = 3,
              pos_pct: float = 0.30, entry_col: str = "fresh_red", rank_col: str = "ret3",
              exit_col: str = "green") -> tuple[pd.DataFrame, pd.DataFrame]:
    ind = ind.set_index(["date", "code"]).sort_index()
    dates = ind.index.get_level_values(0).unique()
    cash, hold, last_px = START_EQUITY, {}, {}
    pend_sell, pend_buy, curve, closed = set(), [], [], []
    for d in dates:
        day = ind.loc[d]
        # Orders decided at yesterday's close execute at today's open.
        for code in list(pend_sell):
            if code not in day.index:
                continue
            r, h = day.loc[code], hold[code]
            lim = r["lim"] if pd.notna(r["lim"]) else limit_pct(code)
            if r["open"] <= last_px[code] * (1 - lim) * 1.005:
                continue
            px = r["open"] * (1 - SLIPPAGE) * (1 - COMMISSION - STAMP_DUTY)
            cash += h["qty"] * px
            closed.append({"code": code, "entry_date": h["date"], "exit_date": d, "reason": h["reason"],
                           "ret": px / h["px"] - 1, "bars": h["bars"]})
            del hold[code]
            pend_sell.discard(code)
        equity_prev = cash + sum(h["qty"] * last_px[c] for c, h in hold.items())
        for code in pend_buy:
            if len(hold) >= max_pos or code in hold or code not in day.index:
                continue
            r = day.loc[code]
            lim = r["lim"] if pd.notna(r["lim"]) else limit_pct(code)
            if r["open"] >= last_px[code] * (1 + lim) * 0.995:
                continue
            px = r["open"] * (1 + SLIPPAGE) * (1 + COMMISSION)
            qty = math.floor(min(equity_prev * pos_pct, cash) / px / 100) * 100
            if qty <= 0:
                continue
            cash -= qty * px
            hold[code] = {"qty": qty, "px": px, "date": d, "bars": 0, "reason": ""}
        pend_buy = []
        # Close of the day: mark to market, then decide tomorrow's orders.
        last_px.update(day["close"].to_dict())
        equity = cash + sum(h["qty"] * last_px[c] for c, h in hold.items())
        curve.append({"date": d, "equity": equity, "n_hold": len(hold),
                      "invested": 1 - cash / equity if equity > 0 else 0.0})
        for code, h in hold.items():
            if code not in day.index:
                continue
            h["bars"] += 1
            r = day.loc[code]
            if bool(r[exit_col]):
                h["reason"] = exit_col
                pend_sell.add(code)
            elif stop_loss is not None and r["close"] <= h["px"] * (1 - stop_loss):
                h["reason"] = "stop_loss"
                pend_sell.add(code)
        cand = day[day[entry_col] & (day["money20"] >= min_money)]
        cand = cand[~cand.index.isin(list(hold))].sort_values(rank_col, ascending=False)
        pend_buy = list(cand.index[: max_pos])
    return pd.DataFrame(curve), pd.DataFrame(closed)


def curve_stats(curve: pd.DataFrame) -> dict:
    eq = curve.set_index("date")["equity"]
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
            "total_return": float(eq.iat[-1] / eq.iat[0] - 1),
            "cagr": float((eq.iat[-1] / eq.iat[0]) ** (1 / years) - 1) if years > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(244)) if r.std() > 0 else None}


def trade_stats(tr: pd.DataFrame) -> dict:
    t = tr[tr["ret"].notna()] if "ret" in tr else tr.iloc[0:0]
    out = {"trades": int(len(t))}
    if len(t):
        out.update({"win_rate": float((t["ret"] > 0).mean()), "mean_ret": float(t["ret"].mean()),
                    "median_ret": float(t["ret"].median()), "p10_ret": float(t["ret"].quantile(0.1)),
                    "p90_ret": float(t["ret"].quantile(0.9)), "share_ge_30pct": float((t["ret"] >= 0.30).mean()),
                    "share_le_minus_10pct": float((t["ret"] <= -0.10).mean()),
                    "mean_bars_held": float(t["bars"].mean())})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df, bench = load()
    ind = indicators(df)
    print(f"universe: {df['code'].nunique()} stocks, {df['date'].min().date()} .. {df['date'].max().date()}")

    summary = {"universe_stocks": int(df["code"].nunique()),
               "fresh_red_signals": int(ind["fresh_red"].sum()),
               "fresh_red_per_month": float(ind.loc[ind["fresh_red"]].groupby(ind["date"].dt.to_period("M")).size().mean())}
    ev = event_study(ind, stop_loss=0.08)
    ev.to_csv(OUT / "universe_event_trades.csv", index=False)
    summary["event_study_stop8"] = {**trade_stats(ev[ev["status"] != "unfilled_limit_up_open"]),
                                    "unfilled_limit_up_open": int((ev["status"] == "unfilled_limit_up_open").sum())}
    ev_ns = event_study(ind, stop_loss=None)
    summary["event_study_no_stop"] = trade_stats(ev_ns[ev_ns["status"] != "unfilled_limit_up_open"])

    variants = {"base_stop8_money50m": (0.08, 5e7), "no_stop_money50m": (None, 5e7), "stop8_no_liquidity_filter": (0.08, 0.0)}
    curves = {}
    for name, (sl, mm) in variants.items():
        curve, closed = portfolio(ind, sl, mm)
        curves[name] = curve.set_index("date")["equity"]
        closed.to_csv(OUT / f"universe_portfolio_trades_{name}.csv", index=False)
        summary[f"portfolio_{name}"] = {**curve_stats(curve), **trade_stats(closed),
                                         "avg_invested": float(curve["invested"].mean())}
        print(name, json.dumps(summary[f"portfolio_{name}"], ensure_ascii=False))

    eq = pd.DataFrame(curves)
    for code, label in BENCH.items():
        b = bench[bench["code"] == code].set_index("date")["close"].reindex(eq.index).ffill()
        eq[label] = START_EQUITY * b / b.dropna().iat[0]
        summary[f"benchmark_{label}"] = curve_stats(eq[[label]].rename(columns={label: "equity"}).reset_index())
    eq.to_csv(OUT / "universe_equity.csv")
    (OUT / "universe_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
