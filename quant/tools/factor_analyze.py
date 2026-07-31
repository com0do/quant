from __future__ import annotations

from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd


def _perf_from_daily_ret(ret: pd.Series) -> dict:
    s = pd.to_numeric(ret, errors="coerce").dropna()
    if s.empty:
        return {"ann_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "days": 0}
    eq = (1.0 + s).cumprod()
    n = len(eq)
    ann = float(eq.iloc[-1] ** (252.0 / max(1, n)) - 1.0)
    dd = eq / eq.cummax() - 1.0
    mdd = float(dd.min()) if not dd.empty else 0.0
    sharpe = float((s.mean() / (s.std(ddof=0) + 1e-12)) * np.sqrt(252.0))
    return {"ann_return": ann, "max_drawdown": mdd, "sharpe": sharpe, "days": int(n)}


def _safe_qcut(s: pd.Series, q: int = 5) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    try:
        return pd.qcut(x, q=q, labels=False, duplicates="drop")
    except Exception:
        return pd.Series(index=s.index, dtype="float64")


def _probe_jqfactor_compat() -> dict:
    report = {"status": "unknown", "error": "", "source": ""}
    # First prefer installed package in current venv.
    try:
        import jqfactor_analyzer as _ja  # noqa: F401

        report["status"] = "import_ok"
        report["source"] = "site-packages"
        return report
    except Exception as exc:
        report["error"] = str(exc)

    # Fallback to optional local third-party checkout.
    tp = Path("third-party/jqfactor_analyzer")
    if not tp.exists():
        report["status"] = "missing"
        return report
    try:
        sys.path.insert(0, str(tp))
        import jqfactor_analyzer as _ja2  # noqa: F401

        report["status"] = "import_ok"
        report["source"] = "third-party"
    except Exception as exc:
        report["status"] = "import_failed"
        report["error"] = str(exc)
    finally:
        if str(tp) in sys.path:
            sys.path.remove(str(tp))
    return report


def run_factor_analyze(db_path: str = "data/market_cache_csi1000_1y.db") -> dict:
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fac = pd.read_sql_query(
        "SELECT date, code, roe, market_cap, pe_ratio, pb_ratio, turnover_ratio FROM factors_snapshot",
        conn,
    )
    px = pd.read_sql_query("SELECT date, code, close FROM prices_daily", conn)
    conn.close()
    if fac.empty or px.empty:
        return {"status": "empty", "rows": 0}
    fac["date"] = pd.to_datetime(fac["date"])
    px["date"] = pd.to_datetime(px["date"])

    # Forward returns as factor target.
    px = px.sort_values(["code", "date"])
    px["ret_fwd_1"] = px.groupby("code")["close"].shift(-1) / px["close"] - 1.0
    merged = fac.merge(px[["date", "code", "ret_fwd_1"]], on=["date", "code"], how="inner")

    factors = ["roe", "market_cap", "pe_ratio", "pb_ratio", "turnover_ratio"]
    ic_rows = []
    layer_rows = []
    turnover_rows = []
    perf_rows = []

    for f in factors:
        dics = []
        factor_spread_daily = []
        prev_top_codes: set[str] = set()
        for d, g in merged.groupby("date", sort=True):
            gg = g[[f, "ret_fwd_1", "code"]].dropna()
            if len(gg) < 20:
                continue
            ic = gg[[f, "ret_fwd_1"]].corr(method="spearman").iloc[0, 1]
            if pd.notna(ic):
                dics.append(float(ic))
            q = _safe_qcut(gg[f], q=5)
            gg = gg.assign(q=q).dropna(subset=["q"])
            if gg.empty:
                continue
            top_ret = float(gg[gg["q"] == gg["q"].max()]["ret_fwd_1"].mean())
            bot_ret = float(gg[gg["q"] == gg["q"].min()]["ret_fwd_1"].mean())
            spread = top_ret - bot_ret
            layer_rows.append({"date": d, "factor": f, "top_quantile_ret1": top_ret, "bottom_quantile_ret1": bot_ret, "spread_ret1": spread})
            factor_spread_daily.append({"date": d, "spread_ret1": spread})
            top_codes = set(gg[gg["q"] == gg["q"].max()]["code"].astype(str).tolist())
            if prev_top_codes:
                turnover = 1.0 - len(top_codes & prev_top_codes) / max(1, len(top_codes | prev_top_codes))
                turnover_rows.append({"date": d, "factor": f, "top_quantile_turnover": turnover})
            prev_top_codes = top_codes
        ic_rows.append(
            {
                "factor": f,
                "ic_mean": float(np.mean(dics)) if dics else 0.0,
                "ic_std": float(np.std(dics, ddof=0)) if dics else 0.0,
                "ir": float(np.mean(dics) / (np.std(dics, ddof=0) + 1e-12)) if dics else 0.0,
                "ic_count": int(len(dics)),
            }
        )
        perf = _perf_from_daily_ret(pd.DataFrame(factor_spread_daily)["spread_ret1"] if factor_spread_daily else pd.Series(dtype="float64"))
        perf_rows.append({"factor": f, **perf})

    ic_df = pd.DataFrame(ic_rows)
    layer_df = pd.DataFrame(layer_rows)
    turnover_df = pd.DataFrame(turnover_rows)
    perf_df = pd.DataFrame(perf_rows)
    merged_perf = ic_df.merge(perf_df, on="factor", how="left")
    positive_factors = (
        merged_perf[
            (merged_perf["ic_mean"] > 0)
            & (merged_perf["ann_return"] > 0)
            & (merged_perf["max_drawdown"] >= -0.20)
        ]["factor"]
        .astype(str)
        .tolist()
    )

    compat = _probe_jqfactor_compat()
    out = {
        "status": "ok",
        "rows": int(len(merged)),
        "factors": factors,
        "positive_factors": positive_factors,
        "jqfactor_compat": compat,
    }
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    ic_df.to_csv("output/factor_ic_ir.csv", index=False)
    layer_df.to_csv("output/factor_layer_returns.csv", index=False)
    turnover_df.to_csv("output/factor_turnover.csv", index=False)
    perf_df.to_csv("output/factor_single_perf.csv", index=False)
    merged_perf.to_csv("output/factor_ranked_summary.csv", index=False)
    Path("output/jqfactor_compat_report.md").write_text(
        "# jqfactor compatibility\n\n```json\n" + json.dumps(compat, ensure_ascii=False, indent=2) + "\n```",
        encoding="utf-8",
    )
    Path("reports/factor_analysis_report.md").write_text(
        "# Factor Analysis Report\n\n"
        f"- db: `{db_path}`\n"
        f"- merged_rows: {len(merged)}\n"
        f"- factors: {', '.join(factors)}\n"
        f"- positive_factors: {', '.join(positive_factors) if positive_factors else 'none'}\n"
        f"- jqfactor_compat: {compat.get('status')}\n"
        f"- outputs: `output/factor_ic_ir.csv`, `output/factor_layer_returns.csv`, `output/factor_turnover.csv`, `output/factor_single_perf.csv`, `output/factor_ranked_summary.csv`\n",
        encoding="utf-8",
    )
    Path("output/factor_feedback.json").write_text(
        json.dumps({"ic_ir": ic_rows, "single_perf": perf_rows, "positive_factors": positive_factors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out
