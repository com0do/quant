"""Measure the deck's 21 case studies against real daily bars.

1. Claim audit: trading days and returns inside each claimed window, under several definitions.
2. Indicator agreement: approximated RED/GREEN vs the bars read from the screenshots.
3. Rule trades: what the approximated signal would actually have captured (next-open execution,
   unfillable when the next day opens at limit-up, costs included).

usage: uv run python research/strong_dragon/measure_cases.py
Writes output/strong_dragon/{claims,agreement,case_trades}.csv and case_summary.json.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from sd_data import HERE, OUT, daily_bars, limit_pct, load_cases
from sd_signals import add_indicator, simulate


def window_stats(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp | None, n_days: int | None) -> dict:
    after = df[df["date"] >= start]
    if end is not None:
        win = after[after["date"] <= end]
    else:
        win = after.head(int(n_days))
    before = df[df["date"] < start]
    if win.empty or before.empty:
        return {}
    prev_close = before["close"].iat[-1]
    run_low = win["low"].cummin()
    return {
        "days": len(win),
        "first": win["date"].iat[0].date().isoformat(),
        "last": win["date"].iat[-1].date().isoformat(),
        "close_ret": win["close"].iat[-1] / prev_close - 1,
        "peak_ret": win["high"].max() / prev_close - 1,
        "low_to_high": float((win["high"] / run_low).max() - 1),
        "one_word_days": int(win["one_word_up"].sum()),
        "limit_up_days": int(win["limit_up"].sum()),
    }


def prf(pred: pd.Series, true: pd.Series) -> tuple[float, float]:
    tp = int((pred & true).sum())
    return tp / max(int(pred.sum()), 1), tp / max(int(true.sum()), 1)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = load_cases()
    sig = pd.read_csv(HERE / "slide_signals.csv")
    claims, agree, trades = [], [], []
    for _, c in cases.iterrows():
        fs = (c["chart_start"] - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
        fe = (c["chart_end"] + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        df = add_indicator(daily_bars(c["code"], fs, fe, c["name"]))
        lim = limit_pct(c["code"], c["name"])

        chart = df[(df["date"] >= c["chart_start"]) & (df["date"] <= c["chart_end"])].reset_index(drop=True)
        s = sig[sig["case_id"] == c["case_id"]].sort_values("bar").reset_index(drop=True)
        if len(s) == len(chart):
            pr, rr = prf(chart["red"], s["red"].astype(bool))
            pg, rg = prf(chart["green"], s["green"].astype(bool))
            agree.append({"case_id": c["case_id"], "name": c["name"], "name_en": c["name_en"], "bars": len(chart),
                          "slide_red": int(s["red"].sum()), "rule_red": int(chart["red"].sum()),
                          "red_precision": pr, "red_recall": rr,
                          "slide_green": int(s["green"].sum()), "rule_green": int(chart["green"].sum()),
                          "green_precision": pg, "green_recall": rg})

        if pd.notna(c["claimed_start"]):
            w = window_stats(df, c["claimed_start"], c["claimed_end"], None)
            n = window_stats(df, c["claimed_start"], None, c["claimed_days"])
            row = {"case_id": c["case_id"], "name": c["name"], "name_en": c["name_en"], "code": c["code"],
                   "claimed_window": f'{c["claimed_start"].date()}..{c["claimed_end"].date()}',
                   "claimed_days": int(c["claimed_days"]), "claimed_ret": c["claimed_return_pct"] / 100,
                   "days_in_window": w.get("days"),
                   "close_ret": w.get("close_ret"), "peak_ret": w.get("peak_ret"), "low_to_high": w.get("low_to_high"),
                   "one_word_days": w.get("one_word_days"),
                   "ndays_last": n.get("last"), "ndays_close_ret": n.get("close_ret"), "ndays_peak_ret": n.get("peak_ret"),
                   "source": df["source"].iat[0]}
            measures = {k: row[k] for k in ("close_ret", "peak_ret", "low_to_high", "ndays_close_ret", "ndays_peak_ret")
                        if row[k] is not None}
            best = min(measures, key=lambda k: abs(measures[k] - row["claimed_ret"]))
            row["closest_measure"], row["closest_value"] = best, measures[best]
            claims.append(row)

            for t in simulate(df, lim):
                sd = pd.Timestamp(t["signal_date"])
                if not (c["chart_start"] <= sd <= c["chart_end"]):
                    continue
                overlaps = (t.get("status") != "unfilled_limit_up_open"
                            and pd.Timestamp(t["entry_date"]) <= c["claimed_end"]
                            and pd.Timestamp(t["exit_date"]) >= c["claimed_start"])
                trades.append({"case_id": c["case_id"], "name": c["name"], "name_en": c["name_en"], "claimed_ret": c["claimed_return_pct"] / 100,
                               "covers_claim": overlaps, **{k: (v.date().isoformat() if isinstance(v, pd.Timestamp) else v)
                                                           for k, v in t.items()}})

    claims_df, agree_df, trades_df = pd.DataFrame(claims), pd.DataFrame(agree), pd.DataFrame(trades)
    claims_df.to_csv(OUT / "claims.csv", index=False)
    agree_df.to_csv(OUT / "agreement.csv", index=False)
    trades_df.to_csv(OUT / "case_trades.csv", index=False)

    closed = trades_df[trades_df["status"].isin(["closed", "open_at_end"])]
    cover = closed[closed["covers_claim"]]
    unfilled = trades_df[trades_df["status"] == "unfilled_limit_up_open"]
    tot = lambda col: float(agree_df[col].sum())
    summary = {
        "cases_with_claims": int(len(claims_df)),
        "claimed_ret_mean": float(claims_df["claimed_ret"].mean()),
        "measured_close_ret_mean": float(claims_df["close_ret"].mean()),
        "measured_peak_ret_mean": float(claims_df["peak_ret"].mean()),
        "measured_low_to_high_mean": float(claims_df["low_to_high"].mean()),
        "claims_with_day_count_mismatch": int((claims_df["days_in_window"] != claims_df["claimed_days"]).sum()),
        "claims_within_10pp_of_any_measure": int(((claims_df["closest_value"] - claims_df["claimed_ret"]).abs() <= 0.10).sum()),
        "red_precision_pooled": float((agree_df["red_precision"] * agree_df["rule_red"]).sum() / max(tot("rule_red"), 1)),
        "red_recall_pooled": float((agree_df["red_recall"] * agree_df["slide_red"]).sum() / max(tot("slide_red"), 1)),
        "green_precision_pooled": float((agree_df["green_precision"] * agree_df["rule_green"]).sum() / max(tot("rule_green"), 1)),
        "green_recall_pooled": float((agree_df["green_recall"] * agree_df["slide_green"]).sum() / max(tot("slide_green"), 1)),
        "rule_trades_in_chart_windows": int(len(closed)),
        "rule_unfilled_signals": int(len(unfilled)),
        "rule_trade_ret_mean": float(closed["ret"].mean()) if len(closed) else None,
        "rule_trade_ret_median": float(closed["ret"].median()) if len(closed) else None,
        "rule_win_rate": float((closed["ret"] > 0).mean()) if len(closed) else None,
        "claims_covered_by_a_rule_trade": int(cover["case_id"].nunique()),
        "covering_trade_ret_mean": float(cover["ret"].mean()) if len(cover) else None,
    }
    (OUT / "case_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    pct = lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.0f}%"
    print("\n| case | stock | claimed window | claimed days / actual | claimed | close-to-close | to window high | low-to-high | one-word limit-ups |")
    print("|---|---|---|---|---|---|---|---|---|")
    for _, r in claims_df.iterrows():
        print(f"| {r.case_id} | {r.name_en} {r.code} | {r.claimed_window} | {r.claimed_days} / {r.days_in_window} | "
              f"{pct(r.claimed_ret)} | {pct(r.close_ret)} | {pct(r.peak_ret)} | {pct(r.low_to_high)} | {r.one_word_days} |")
    print("\n| case | stock | slide red / rule red | red precision | red recall | slide green / rule green | green precision | green recall |")
    print("|---|---|---|---|---|---|---|---|")
    for _, r in agree_df.iterrows():
        print(f"| {r.case_id} | {r.name_en} | {r.slide_red} / {r.rule_red} | {pct(r.red_precision)} | {pct(r.red_recall)} | "
              f"{r.slide_green} / {r.rule_green} | {pct(r.green_precision)} | {pct(r.green_recall)} |")
    print("\n| case | stock | signal | entry | exit | reason | rule return | peak after entry | claimed |")
    print("|---|---|---|---|---|---|---|---|---|")
    for _, r in cover.iterrows():
        print(f"| {r.case_id} | {r.name_en} | {r.signal_date} | {r.entry_date} | {r.exit_date} | {r.reason} | "
              f"{pct(r.ret)} | {pct(r.peak_ret)} | {pct(r.claimed_ret)} |")
    print("\nsummary:", json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
