"""Do news or concept catalysts separate the deck's winners from the other RED signals?

Hypothesis: the team hand-picks RED stocks that have a stock-specific catalyst (an announcement such as a change
of control, a restructuring, an earnings jump or a big contract) or that sit in a concept (theme) that is moving.

Inputs come from fetch_catalysts.py: cninfo announcement titles, Eastmoney earnings forecasts and Eastmoney concept
tags. Concept heat on day t = median 5-day return and limit-up count of the concept's members in the local universe
(CSI 1000 + CSI 2000 databases), so it only uses prices up to t.
Point in time: a signal on day t only sees announcements dated t or earlier (cninfo dates evening releases with
their publication day, and the trade happens at the t+1 open). The concept *membership* is today's snapshot,
which favours the concept hypothesis (see fetch_catalysts.py).

Variants (entry = fresh RED plus the condition; exit, stop, costs and portfolio rules as in sector_study.py),
reported on train 2025-01..2025-08 and test 2025-09..2026-03. Thresholds are fixed up front.

usage: uv run python research/strong_dragon/catalyst_study.py   (after fetch_catalysts.py)
Writes output/strong_dragon/catalyst_*.csv and catalyst_summary.json.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from fetch_catalysts import ANN_DIR, CON_DIR
from scan_universe import curve_stats, indicators, load, portfolio
from sd_data import CACHE, HERE, OUT, daily_bars, limit_pct, load_cases
from sd_signals import add_indicator
from sector_study import SPLIT, STOP_LOSS, add_features, fast_trades

WINDOW_DAYS = 7
HOT_CONCEPT_TOP = 10
MIN_THEME_PEERS = 2
MIN_CONCEPT_MEMBERS = 8
LIMIT_UP_WAVE = 3

# Regexes over cninfo announcement titles, so they must stay Chinese. Positive catalysts: change of control
# (incl. tender offers and share transfers), new restructuring / asset-purchase plans, sales contracts and bids won,
# new buyback / stake-increase plans, new incentive plans (drafts), approvals / new products. Routine follow-ups
# (progress notices, lock-up expiries, legal opinions on old deals) are deliberately not matched.
TITLE_CATALYSTS = {
    "control": (r"控制权|实际控制人.{0,4}(?:拟)?(?:发生)?变更|控股股东.{0,4}(?:拟)?发生变更|要约收购(?!.*持续督导)"
                r"|协议转让.{0,8}股份|股份.{0,8}协议转让"),
    "restructure": r"重大资产重组|筹划.{0,12}(?:重组|购买|收购)|购买资产.{0,30}(?:预案|草案)|吸收合并",
    "contract": r"中标|重大合同|重大.{0,4}订单|签订.{0,16}(?:销售|供货|战略合作|框架).{0,6}(?:合同|协议)|战略合作协议",
    "buyback": r"提议回购|回购股份方案|回购股份报告书|回购方案|增持计划(?!.*(?:进展|完成|完毕|结果|届满))|拟增持",
    "incentive": r"(?:激励计划|持股计划).{0,3}草案",
    "approval": r"获批|获得.{0,10}(?:注册证|批准|认证)|取得.{0,10}(?:证书|许可)|新产品|临床",
}
# Negative news: planned share sales, unanswered regulator letters, investigations, delisting risk, deals terminated.
NEGATIVE = (r"拟减持|减持计划(?!.*(?:终止|完毕|完成|结果|届满))|(?:问询函|关注函)(?!.*(?:回复|核查意见|说明))|监管函|警示函"
            r"|立案|行政处罚|处罚决定|退市风险|终止.{0,12}(?:重组|收购|购买|转让|发行|控制权)")
# Reaction notices the exchange requires after a sharp move; they follow the price, they do not cause it.
REACTION = r"异常波动|交易风险提示|风险提示性公告|股价异动|停牌核查"
# Eastmoney forecast types that count as "earnings up" (net profit to shareholders: up sharply / back to profit).
FORECAST_UP = ("预增", "扭亏")
FORECAST_BASIS = "归属于上市公司股东的净利润"
CATALYSTS = list(TITLE_CATALYSTS) + ["earnings_up"]


def load_announcements() -> pd.DataFrame:
    parts = [pd.read_csv(p, parse_dates=["time"]) for p in ANN_DIR.glob("*.csv")]
    ann = pd.concat([p for p in parts if len(p)], ignore_index=True).drop_duplicates(["code", "ann_id"])
    ann["day"] = ann["time"].dt.normalize()
    for k, pat in TITLE_CATALYSTS.items():
        ann[k] = ann["title"].str.contains(pat, regex=True)
    ann["earnings_up"] = False
    ann["negative"] = ann["title"].str.contains(NEGATIVE, regex=True)
    ann["reaction"] = ann["title"].str.contains(REACTION, regex=True)

    fc = pd.read_csv(CACHE / "forecast.csv", dtype={"SECURITY_CODE": str})
    fc = fc[(fc["PREDICT_FINANCE"] == FORECAST_BASIS) & fc["PREDICT_TYPE"].isin(FORECAST_UP)]
    num = fc["SECURITY_CODE"].str.zfill(6)
    fc = pd.DataFrame({"code": num + np.where(num.str[0] == "6", ".XSHG", np.where(num.str[0].isin(["0", "3"]), ".XSHE", ".BJ")),
                       "time": pd.to_datetime(fc["NOTICE_DATE"]), "title": "forecast: " + fc["PREDICT_TYPE"],
                       "ann_id": "fc_" + num + "_" + fc["REPORT_DATE"].str[:10]})
    fc = fc[fc["code"].isin(set(ann["code"]))].drop_duplicates("ann_id")
    fc["day"] = fc["time"].dt.normalize()
    ann = pd.concat([ann, fc], ignore_index=True)
    flags = CATALYSTS + ["negative", "reaction"]
    ann[flags] = ann[flags].fillna(False).astype(bool)
    ann.loc[ann["ann_id"].astype(str).str.startswith("fc_"), "earnings_up"] = True
    ann["catalyst"] = ann[CATALYSTS].any(axis=1) & ~ann["negative"]
    return ann


def window_flags(sig: pd.DataFrame, ann: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """For each (code, date) row: does any announcement with flag c fall in [date - WINDOW_DAYS, date]?"""
    out = {c: np.zeros(len(sig), dtype=bool) for c in cols}
    out["n_ann"] = np.zeros(len(sig), dtype=int)
    by_code = {c: g.sort_values("day") for c, g in ann.groupby("code")}
    for code, idx in sig.groupby("code").indices.items():
        a = by_code.get(code)
        if a is None:
            continue
        days = a["day"].to_numpy()
        d = sig["date"].to_numpy()[idx]
        lo = np.searchsorted(days, d - np.timedelta64(WINDOW_DAYS, "D"), side="left")
        hi = np.searchsorted(days, d, side="right")
        out["n_ann"][idx] = hi - lo
        for c in cols:
            cum = np.concatenate([[0], np.cumsum(a[c].to_numpy())])
            out[c][idx] = (cum[hi] - cum[lo]) > 0
    return pd.DataFrame(out, index=sig.index)


def concept_heat(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per (date, concept): median 5-day return, limit-up count and rank among concepts, from universe members."""
    tags = pd.read_csv(CON_DIR / "tags.csv", dtype={"precise": str})
    tags = tags.loc[tags["precise"] == "1", ["code", "bk", "name"]].drop_duplicates(["code", "bk"])
    members = tags[tags["code"].isin(set(df["code"]))]
    members = members[members.groupby("bk")["code"].transform("size") >= MIN_CONCEPT_MEMBERS]
    x = df[["code", "date", "ret5", "limit_up"]].merge(members[["code", "bk"]], on="code")
    heat = x.groupby(["date", "bk"]).agg(c_ret5=("ret5", "median"), c_up=("limit_up", "sum")).reset_index()
    heat["rank"] = heat.groupby("date")["c_ret5"].rank(ascending=False, method="min")
    heat["boards"] = heat.groupby("date")["bk"].transform("size")
    return tags, heat


def concept_features(sig: pd.DataFrame, tags: pd.DataFrame, heat: pd.DataFrame) -> pd.DataFrame:
    x = sig[["code", "date", "limit_up"]].reset_index().merge(tags, on="code").merge(heat, on=["bk", "date"])
    best = x.sort_values("rank").drop_duplicates("index").set_index("index")
    x["peers"] = x.groupby(["date", "bk"])["code"].transform("size") - 1
    x["others_up"] = x["c_up"] - x["limit_up"].astype(int)
    agg = x.groupby("index").agg(theme_peers=("peers", "max"), others_up=("others_up", "max"))
    out = pd.DataFrame(index=sig.index)
    out["concept_rank"] = best["rank"]
    out["concept_name"] = best["name"]
    out = out.join(agg)
    out[["theme_peers", "others_up"]] = out[["theme_peers", "others_up"]].fillna(0).astype(int)
    return out


def stats(tr: pd.DataFrame) -> dict:
    t = tr[tr["status"] != "unfilled_limit_up_open"] if len(tr) and "status" in tr else tr
    if len(t) == 0 or "ret" not in t:
        return {"trades": 0}
    r = t["ret"]
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean()), 3), "mean_ret": round(float(r.mean()), 4),
            "median_ret": round(float(r.median()), 4), "t_stat": round(float(r.mean() / se), 2) if se and se > 0 else None,
            "share_ge_30pct": round(float((r >= 0.30).mean()), 3)}


def _tags_of(a: pd.DataFrame) -> str:
    return ",".join([k for k in CATALYSTS if a[k].any()] + [k for k in ("negative", "reaction") if a[k].any()])


def case_check(ann: pd.DataFrame, tags: pd.DataFrame, heat: pd.DataFrame) -> pd.DataFrame:
    cases = load_cases().dropna(subset=["claimed_start"])
    rows = []
    for _, c in cases.iterrows():
        fs = (c["chart_start"] - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
        fe = (c["chart_end"] + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        s = add_indicator(daily_bars(c["code"], fs, fe, c["name"]))
        win = s[(s["date"] >= c["claimed_start"] - pd.Timedelta(days=10)) & (s["date"] <= c["claimed_end"]) & s["red"]]
        if win.empty:
            continue
        t = win["date"].iat[0]
        a = ann[ann["code"] == c["code"]]
        before = a[(a["day"] >= t - pd.Timedelta(days=WINDOW_DAYS)) & (a["day"] <= t)]
        during = a[(a["day"] > t) & (a["day"] <= c["claimed_end"])]
        cr = tags[tags["code"] == c["code"]].merge(heat[heat["date"] == t], on="bk").sort_values("rank")
        rows.append({"case_id": c["case_id"], "name_en": c["name_en"], "code": c["code"], "first_red": t.date().isoformat(),
                     "catalyst_before": bool(before["catalyst"].any()), "tags_before": _tags_of(before),
                     "titles_before": " | ".join(before.loc[before["catalyst"], "title"].head(3)),
                     "tags_during": _tags_of(during),
                     "titles_during": " | ".join(during.loc[during["catalyst"], "title"].head(2)),
                     "best_concept": cr["name"].iat[0] if len(cr) else None,
                     "best_concept_rank": int(cr["rank"].iat[0]) if len(cr) else None,
                     "boards": int(cr["boards"].iat[0]) if len(cr) else None,
                     "max_concept_limit_ups": int(cr["c_up"].max()) if len(cr) else None})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    industry = pd.read_csv(HERE / "industry_sw.csv")
    px, bench = load()
    df = add_features(indicators(px), industry)
    lim = df["lim"].fillna(df["code"].map({c: limit_pct(c) for c in df["code"].unique()}))
    df["limit_up"] = (df["close"] / df.groupby("code")["close"].shift(1) - 1) >= lim - 0.005
    ann = load_announcements()
    tags, heat = concept_heat(df)
    covered = set(ann["code"]) & set(df.loc[df["fresh_red"], "code"])
    print(f"announcements: {len(ann)} over {ann['code'].nunique()} stocks; catalyst rows {int(ann['catalyst'].sum())}; "
          f"concepts ranked: {heat['bk'].nunique()}; signal stocks covered: {len(covered)}")

    df = df[df["code"].isin(covered)].reset_index(drop=True)
    flags = CATALYSTS + ["catalyst", "negative", "reaction"]
    df = df.join(window_flags(df[["code", "date"]], ann, flags))
    sig = df["fresh_red"]
    df = df.join(concept_features(df.loc[sig, ["code", "date", "limit_up"]], tags, heat), how="left")
    df[["theme_peers", "others_up"]] = df[["theme_peers", "others_up"]].fillna(0)

    base = {"all_stock_days": float(df["catalyst"].mean()), "fresh_red_days": float(df.loc[sig, "catalyst"].mean()),
            "fresh_red_reaction_notice": float(df.loc[sig, "reaction"].mean()),
            "fresh_red_hot_concept": float((df.loc[sig, "concept_rank"] <= HOT_CONCEPT_TOP).mean())}
    print("base rates:", json.dumps({k: round(v, 3) for k, v in base.items()}))

    hot = df["concept_rank"] <= HOT_CONCEPT_TOP
    theme = df["theme_peers"] >= MIN_THEME_PEERS
    cat, major = df["catalyst"], df["control"] | df["restructure"]
    entries = {"all": sig, "catalyst": sig & cat, "no catalyst": sig & ~cat, "control/restructure": sig & major,
               "earnings up": sig & df["earnings_up"], "contract": sig & df["contract"],
               "reaction notice only": sig & df["reaction"] & ~cat, "negative news": sig & df["negative"],
               "hot concept": sig & hot, "theme cluster": sig & theme,
               "limit-up wave": sig & (df["others_up"] >= LIMIT_UP_WAVE),
               "catalyst + hot concept": sig & cat & hot, "catalyst, no theme cluster": sig & cat & ~theme}
    halves = {"train": df["date"] < SPLIT, "test": df["date"] >= SPLIT}
    rows, summary = [], {"split": SPLIT.date().isoformat(), "window_days": WINDOW_DAYS, "hot_concept_top": HOT_CONCEPT_TOP,
                         "min_theme_peers": MIN_THEME_PEERS, "min_concept_members": MIN_CONCEPT_MEMBERS,
                         "limit_up_wave": LIMIT_UP_WAVE, "base_rates": base}
    for name, mask in entries.items():
        col = "e_" + re.sub(r"\W+", "_", name).strip("_")
        df[col] = mask.fillna(False).astype(bool)
        tr = fast_trades(df, col)
        sd = pd.to_datetime(tr["signal_date"]) if len(tr) else pd.Series(dtype="datetime64[ns]")
        res = {}
        for half, part in halves.items():
            ev = stats(tr[(sd < SPLIT) if half == "train" else (sd >= SPLIT)] if len(tr) else tr)
            curve, _ = portfolio(df[part], STOP_LOSS, 5e7, entry_col=col, rank_col="ret5")
            pf = curve_stats(curve)
            res[half] = {"event": ev, "portfolio": {k: round(pf[k], 3) for k in ("total_return", "max_drawdown")}}
            rows.append({"variant": name, "half": half, "signals": int(df.loc[part, col].sum()), **ev,
                         "pf_total_return": round(pf["total_return"], 3), "pf_max_drawdown": round(pf["max_drawdown"], 3)})
        summary[name] = res
    for code, label in {"000852.XSHG": "CSI 1000", "399303.XSHE": "CNI 2000"}.items():
        b = bench[bench["code"] == code].set_index("date")["close"]
        summary[f"benchmark_{label}"] = {h: float(b[(b.index >= df.loc[m, 'date'].min()) & (b.index <= df.loc[m, 'date'].max())]
                                                 .iloc[[0, -1]].pct_change().iat[-1]) for h, m in halves.items()}
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "catalyst_variants.csv", index=False)
    cc = case_check(ann, tags, heat)
    cc.to_csv(OUT / "catalyst_case_check.csv", index=False)
    summary["case_check"] = cc.to_dict(orient="records")
    (OUT / "catalyst_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    pd.set_option("display.width", 250)
    print(table.to_string(index=False))
    print(cc.drop(columns=["titles_before", "titles_during"]).to_string(index=False))
    print("benchmarks:", {k: v for k, v in summary.items() if k.startswith("benchmark")})


if __name__ == "__main__":
    main()
