"""Recent-window news / policy / announcement / fundamentals selector and backtest.

The thesis: a main up-wave is an emotional response to good news. Instead of the deck's hindsight charts, this
selector combines, at the close of day t:
  - corporate announcement catalysts published up to t-1 (positive minus negative),
  - CCTV policy news mapped to Shenwan level-2 industries, up to t-1,
  - Tonghuashun limit-up reasons as an industry heat proxy, up to t-1,
  - the latest published quarterly fundamentals (ROE, revenue/profit growth, margin, leverage),
  - a technical confirmation (MA alignment, 5-day strength, volume, distance to the 20-day high).
It then buys the top names at the next open, with 30% per name, at most 3 names, a 5-day time exit, a green-bar exit
and an 8% stop.

Data lives in `data/` (gitignored) and is read through `quant.stock_data.news_store.NewsStore`; the strategy code
is here, so the data layer and the strategy stay separate.

usage:
  uv run --with akshare python research/news_selector/selector.py \
      --test-start 2026-06-01 --end 2026-10-26 --limit 800
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

from classify import classify_announcements_frame, classify_limit_up_reason, classify_policy_frame  # noqa: E402
from quant.stock_data.news_store import NewsStore  # noqa: E402

OUT = ROOT / "output" / "news_selector"
KLINE = ROOT / "data" / "market" / "kline"
if not KLINE.exists():
    KLINE = ROOT / "output" / "strong_dragon" / "kline"
INDEX = ROOT / "data" / "market" / "kline_index" / "000852.XSHG.csv"
if not INDEX.exists():
    INDEX = ROOT / "output" / "strong_dragon" / "kline_index" / "000852.XSHG.csv"
UNIVERSE = ROOT / "data" / "market" / "selector_universe_pit.csv"
COMMISSION, STAMP_DUTY, SLIPPAGE = 0.0003, 0.0005, 0.001
MAX_POS, POS_PCT, MAX_HOLD, STOP = 3, 0.30, 5, 0.08
MIN_AMOUNT = 5e7
START_EQUITY = 1_000_000.0


def limit_for(code: str, is_st: bool) -> float:
    num = code.partition(".")[0]
    if is_st:
        return 0.05
    if num.startswith(("300", "301", "688", "689")):
        return 0.20
    if num.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def load_universe(limit: int, path: Path = UNIVERSE) -> list[str]:
    if not path.exists():
        raise SystemExit(f"universe not found: {path}; run research/news_selector/build_universe.py --asof 2026-03-01")
    rows = pd.read_csv(path, dtype={"code": str})
    return rows["code"].head(limit).tolist()


def load_panel(codes: list[str], start: str, end: str, sw: dict[str, str]) -> pd.DataFrame:
    frames = []
    for code in codes:
        path = KLINE / f"{code}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        df["code"] = code
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if len(df) < 30:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        c = df["close"]
        for n in (5, 10, 20):
            df[f"ma{n}"] = c.rolling(n).mean()
        df["ret5"] = c / c.shift(5) - 1
        df["ret20"] = c / c.shift(20) - 1
        df["vol_ratio20"] = df["volume"] / df["volume"].rolling(20).mean()
        df["amount20"] = df["amount"].rolling(20).mean()
        df["close_ma20"] = c / df["ma20"] - 1
        df["dist_high20"] = c / df["high"].rolling(20).max() - 1
        df["lim"] = [limit_for(code, bool(x)) for x in df["isST"].fillna(0)]
        df["limit_up"] = (c / df["preclose"] - 1) >= (df["lim"] - 0.005)
        df["green"] = (c < df["ma5"] * (1 - 1e-9)) & (df["ma5"] < df["ma10"])
        df["fresh_red"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["ret5"] >= 0.05)
        df["sector"] = sw.get(code, "")
        frames.append(df[["date", "code", "open", "high", "low", "close", "preclose", "lim", "limit_up",
                          "green", "fresh_red", "ret5", "ret20", "vol_ratio20", "amount20", "close_ma20",
                          "dist_high20", "isST", "sector"]])
    panel = pd.concat(frames, ignore_index=True)
    return panel.sort_values(["code", "date"]).reset_index(drop=True)


def _next_trade(dates: pd.DatetimeIndex, value: str) -> pd.Timestamp | None:
    d = pd.Timestamp(value)
    i = dates.searchsorted(d, side="left")
    return dates[i] if i < len(dates) else None


def add_news(panel: pd.DataFrame, store: NewsStore, start: str, end: str) -> pd.DataFrame:
    dates = pd.DatetimeIndex(sorted(panel["date"].unique()))

    ann = store.announcements(start, end)
    code_map = {c.partition(".")[0]: c for c in panel["code"].unique()}
    if not ann.empty:
        ann = classify_announcements_frame(ann)
        ann = ann[ann["score"] != 0].copy()
        ann["code"] = ann["code"].map(code_map)
        ann = ann.dropna(subset=["code"])
        ann["trade_date"] = [_next_trade(dates, d) for d in ann["date"]]
        ann = ann.dropna(subset=["trade_date"])
        daily = ann.groupby(["trade_date", "code"]).agg(
            ann_pos=("score", lambda s: float(s[s > 0].sum())),
            ann_neg=("score", lambda s: float(s[s < 0].min()) if (s < 0).any() else 0.0),
            ann_n=("score", "size")).reset_index().rename(columns={"trade_date": "date"})
        panel = panel.merge(daily, on=["date", "code"], how="left")
    for col in ("ann_pos", "ann_neg", "ann_n"):
        if col not in panel:
            panel[col] = 0.0
    panel[["ann_pos", "ann_neg", "ann_n"]] = panel[["ann_pos", "ann_neg", "ann_n"]].fillna(0.0)
    panel = panel.sort_values(["code", "date"])
    panel["ann_pos_5"] = panel.groupby("code")["ann_pos"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum())
    panel["ann_neg_10"] = panel.groupby("code")["ann_neg"].transform(
        lambda s: s.shift(1).rolling(10, min_periods=1).min())

    pol = store.policy_news(start, end)
    if not pol.empty:
        pol = classify_policy_frame(pol)
        pol = pol[pol["score"] > 0].copy()
        pol = pol.explode("themes").dropna(subset=["themes"])
        pol["sector"] = pol["themes"].map(lambda t: list(__import__("classify").THEME_TO_SW2.get(t, set())))
        pol = pol.explode("sector").dropna(subset=["sector"])
        pol["trade_date"] = [_next_trade(dates, d) for d in pol["date"]]
        pol = pol.dropna(subset=["trade_date"])
        pol_daily = pol.groupby(["trade_date", "sector"])["score"].sum().reset_index().rename(
            columns={"trade_date": "date", "score": "policy_daily"})
        panel = panel.merge(pol_daily, on=["date", "sector"], how="left")
    if "policy_daily" not in panel:
        panel["policy_daily"] = 0.0
    panel["policy_daily"] = panel["policy_daily"].fillna(0.0)
    panel = panel.sort_values(["code", "date"])
    panel["policy_score"] = panel.groupby("code")["policy_daily"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum())

    lu = store.limit_up(start, end)
    if not lu.empty:
        lu["code"] = lu["code"].map(code_map)
        lu = lu.dropna(subset=["code"])
        lu["trade_date"] = [_next_trade(dates, d) for d in lu["date"]]
        lu = lu.dropna(subset=["trade_date"])
        lu = lu.merge(panel[["code", "sector"]].drop_duplicates("code"), on="code", how="left")
        heat = lu.groupby(["trade_date", "sector"]).size().reset_index(name="industry_limit").rename(
            columns={"trade_date": "date"})
        panel = panel.merge(heat, on=["date", "sector"], how="left")
        market = lu.groupby("trade_date").size().reset_index(name="market_limit").rename(
            columns={"trade_date": "date"})
        panel = panel.merge(market, on="date", how="left")
    for col in ("industry_limit", "market_limit"):
        if col not in panel:
            panel[col] = 0.0
    panel[["industry_limit", "market_limit"]] = panel[["industry_limit", "market_limit"]].fillna(0.0)
    panel = panel.sort_values(["code", "date"])
    panel["industry_heat"] = panel.groupby("code")["industry_limit"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum())
    return panel


def add_fundamentals(panel: pd.DataFrame, store: NewsStore) -> pd.DataFrame:
    fund = store.fundamentals()
    cols = ["roe", "revenue_yoy", "profit_yoy", "gross_margin", "debt_ratio"]
    if fund.empty:
        for c in cols:
            panel[c] = np.nan
        return panel
    fund["pub_date"] = pd.to_datetime(fund["pub_date"])
    fund = fund.sort_values(["code", "pub_date", "report_date"]).drop_duplicates(["code", "pub_date"], keep="last")
    parts = []
    for code, g in panel.groupby("code", sort=False):
        g = g.sort_values("date")
        f = fund[fund["code"] == code].sort_values("pub_date")
        if f.empty:
            for c in cols:
                g[c] = np.nan
        else:
            g = pd.merge_asof(g, f[["pub_date"] + cols], left_on="date", right_on="pub_date",
                              direction="backward")
        parts.append(g)
    return pd.concat(parts, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)


def zscore(panel: pd.DataFrame, col: str) -> pd.Series:
    g = panel.groupby("date")[col]
    return ((panel[col] - g.transform("mean")) / (g.transform("std") + 1e-9)).fillna(0.0)


def score_panel(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["news_score"] = zscore(p, "ann_pos_5") + 0.5 * zscore(p, "policy_score") + 0.5 * zscore(p, "industry_heat")
    p["fund_score"] = (zscore(p, "revenue_yoy") + zscore(p, "profit_yoy") + zscore(p, "roe")
                       + zscore(p, "gross_margin") - zscore(p, "debt_ratio"))
    p["tech_score"] = (zscore(p, "ret5") + zscore(p, "vol_ratio20") + zscore(p, "close_ma20")
                       + zscore(p, "dist_high20") + p["fresh_red"].astype(float))
    p["score"] = 0.45 * p["news_score"] + 0.30 * p["fund_score"] + 0.25 * p["tech_score"]
    p["liquid"] = (p["amount20"] >= MIN_AMOUNT) & (p["close"] > 1) & (p["isST"].fillna(0) == 0)
    return p


def signals_for(p: pd.DataFrame, variant: str) -> pd.DataFrame:
    base = p[p["liquid"]].copy()

    def z(col: str) -> pd.Series:
        g = base.groupby("date")[col]
        return ((base[col] - g.transform("mean")) / (g.transform("std") + 1e-9)).fillna(0.0)

    if variant == "combined":
        s = base[(base["news_score"] > 0) & (base["tech_score"] > 0) & (base["fund_score"] > -1.0)]
        s = s.assign(rank_score=s["score"])
    elif variant == "policy_tech":
        s = base[(base["policy_score"] > 0) & (base["tech_score"] > 0)]
        s = s.assign(rank_score=0.7 * z("policy_score") + 0.3 * z("tech_score"))
    elif variant == "multi":
        s = base[(base["policy_score"] > 0) & (base["tech_score"] > 0)
                 & (base["ann_neg_10"] == 0) & (base["fund_score"] > -1.0)]
        s = s.assign(rank_score=(0.55 * z("policy_score") + 0.20 * z("tech_score")
                                 + 0.15 * z("fund_score") + 0.10 * z("ann_pos_5")))
    elif variant == "ann_fund":
        s = base[(base["ann_pos_5"] > 0) & (base["ann_neg_10"] == 0)
                 & (base["tech_score"] > 0) & (base["fund_score"] > 0)]
        s = s.assign(rank_score=z("ann_pos_5") + 0.5 * z("tech_score") + 0.2 * z("fund_score"))
    elif variant == "announcement":
        s = base[(base["ann_pos_5"] > 0) & (base["tech_score"] > 0) & (base["ann_neg_10"] == 0)]
        s = s.assign(rank_score=s["ann_pos_5"] + 0.5 * s["tech_score"])
    elif variant == "policy":
        s = base[(base["policy_score"] > 0) & (base["tech_score"] > 0)]
        s = s.assign(rank_score=s["policy_score"] + 0.5 * s["tech_score"])
    elif variant == "fundamental":
        s = base[(base["tech_score"] > 0) & (base["fund_score"] > 0)]
        s = s.assign(rank_score=s["fund_score"])
    else:
        s = base[base["tech_score"] > 0]
        s = s.assign(rank_score=s["tech_score"])
    return s[["date", "code", "rank_score"]].dropna()


def backtest(panel: pd.DataFrame, sig: pd.DataFrame, test_start: str, test_end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = panel[(panel["date"] >= test_start) & (panel["date"] <= test_end)]
    dates = sorted(p["date"].unique())
    by_code = {c: g.set_index("date") for c, g in p.groupby("code", sort=False)}
    s = sig[(sig["date"] >= test_start) & (sig["date"] <= test_end)].sort_values("rank_score", ascending=False)
    by_date = {d: list(zip(g["code"], g["rank_score"])) for d, g in s.groupby("date")}
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
            if h["bars"] >= MAX_HOLD:
                h["reason"] = "max_hold"
                pend_sell.add(code)
            elif bool(r["green"]):
                h["reason"] = "green"
                pend_sell.add(code)
            elif r["close"] <= h["px"] * (1 - STOP):
                h["reason"] = "stop"
                pend_sell.add(code)
        equity = cash + sum(h["qty"] * last_px.get(c, h["px"]) for c, h in hold.items())
        curve.append({"date": d, "equity": equity, "n_hold": len(hold)})
        cand = [(c, sc) for c, sc in by_date.get(d, []) if c not in hold]
        pend_buy = cand[:MAX_POS]
    return pd.DataFrame(curve), pd.DataFrame(closed)


def stats(curve: pd.DataFrame, closed: pd.DataFrame) -> dict:
    if curve.empty:
        return {}
    eq = curve.set_index("date")["equity"]
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    out = {"total_return": float(eq.iat[-1] / eq.iat[0] - 1),
           "max_drawdown": float((eq / eq.cummax() - 1).min()),
           "sharpe": float(r.mean() / r.std() * math.sqrt(244)) if r.std() > 0 else None,
           "trades": int(len(closed))}
    if len(closed):
        out.update({"win_rate": float((closed["ret"] > 0).mean()),
                    "mean_ret": float(closed["ret"].mean()),
                    "median_ret": float(closed["ret"].median()),
                    "mean_bars": float(closed["bars"].mean())})
        if len(closed) > 1 and closed["ret"].std() > 0:
            out["t_stat"] = float(closed["ret"].mean() / (closed["ret"].std(ddof=1) / math.sqrt(len(closed))))
    return out


def benchmark(panel: pd.DataFrame, test_start: str, test_end: str) -> dict:
    p = panel[(panel["date"] >= test_start) & (panel["date"] <= test_end)]
    ret = (p["close"] / p["preclose"] - 1).clip(-0.5, 0.5)
    ew = p.assign(ret=ret).groupby("date")["ret"].mean()
    eq = (1 + ew).cumprod()
    out = {"equal_weight": float(eq.iat[-1] - 1),
           "equal_weight_mdd": float((eq / eq.cummax() - 1).min())}
    if INDEX.exists():
        idx = pd.read_csv(INDEX, parse_dates=["date"]).set_index("date")["close"].sort_index()
        idx = idx[(idx.index >= test_start) & (idx.index <= test_end)]
        if len(idx):
            out["csi1000"] = float(idx.iat[-1] / idx.iat[0] - 1)
            out["csi1000_mdd"] = float((idx / idx.cummax() - 1).min())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-03-01", help="panel/news warmup")
    ap.add_argument("--test-start", default="2026-06-01")
    ap.add_argument("--end", default="2026-10-26")
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--universe", default=str(UNIVERSE))
    ap.add_argument("--db", default=str(ROOT / "data" / "news" / "news.db"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    store = NewsStore(args.db)
    print("[store]", store.counts())
    sw = dict(pd.read_csv(ROOT / "research" / "strong_dragon" / "industry_sw.csv",
                          dtype={"code": str}).set_index("code")["sw1"])
    codes = load_universe(args.limit, Path(args.universe))
    print(f"[universe] {len(codes)} stocks")
    panel = load_panel(codes, args.start, args.end, sw)
    print(f"[panel] {panel['code'].nunique()} stocks, {len(panel):,} rows")
    panel = add_news(panel, store, args.start, args.end)
    panel = add_fundamentals(panel, store)
    panel = score_panel(panel)
    summary = {"data": {"stocks": int(panel["code"].nunique()), "rows": int(len(panel)),
                        "start": str(panel["date"].min().date()), "end": str(panel["date"].max().date()),
                        "test_start": args.test_start, "test_end": args.end},
               "store": store.counts(), "benchmark": benchmark(panel, args.test_start, args.end), "variants": {}}
    for variant in ("tech", "announcement", "ann_fund", "policy", "policy_tech", "fundamental", "multi", "combined"):
        sig = signals_for(panel, variant)
        curve, closed = backtest(panel, sig, args.test_start, args.end)
        summary["variants"][variant] = {**stats(curve, closed), "signals": int(len(sig))}
        if not curve.empty:
            curve.to_csv(OUT / f"equity_{variant}.csv", index=False)
            closed.to_csv(OUT / f"trades_{variant}.csv", index=False)
        print(variant, json.dumps(summary["variants"][variant], ensure_ascii=False))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[done]", OUT / "summary.json")


if __name__ == "__main__":
    main()
