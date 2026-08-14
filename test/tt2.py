# %%
import matplotlib.pyplot as plt
import pandas as pd
import datetime
from pathlib import Path
import warnings

from quant.stock_data.jq_client import JqClient

warnings.filterwarnings("ignore", category=Warning, module=r"jqdatasdk\.compat\.pickle_compat")


jq_client = JqClient()


# %%
asof = "2025-12-31"
industry_code = "A01"
tracked_codes = ["000998.XSHE", "002772.XSHE", "600108.XSHG"]
bar_count = 120
output_path = Path("output") / "tt2_a01_multi_ema.png"


def run_industry_ema_experiment(
    *,
    asof: str = asof,
    industry_code: str = industry_code,
    tracked_codes: list[str] | None = None,
    bar_count: int = bar_count,
    output_path: Path = output_path,
):
    with jq_client.session() as jq_api:
        stocks = jq_api.get_industry_stocks(industry_code, date=asof)
        if not stocks:
            raise RuntimeError(f"no stocks returned for industry={industry_code} at {asof}")
        price_df = jq_api.get_price(
            stocks,
            end_date=asof,
            count=bar_count,
            frequency="daily",
            fields=["close"],
            panel=False,
        )

    close_wide = price_df.pivot(index="time", columns="code", values="close").sort_index()
    chosen_codes = [code for code in (tracked_codes or []) if code in close_wide.columns]
    if not chosen_codes:
        chosen_codes = list(close_wide.columns[:3])
    tracked_close_wide = close_wide[chosen_codes].astype(float)
    ema20_wide = tracked_close_wide.ewm(span=20, adjust=False).mean()
    ema5_wide = tracked_close_wide.ewm(span=5, adjust=False).mean()

    fig, axes = plt.subplots(len(chosen_codes), 1, figsize=(10, 3.8 * len(chosen_codes)), sharex=True)
    if len(chosen_codes) == 1:
        axes = [axes]
    for ax, code in zip(axes, chosen_codes):
        tracked_close_wide[code].dropna().plot(ax=ax, label=f"{code} close")
        ema20_wide[code].dropna().plot(ax=ax, label="20 day EMA")
        ema5_wide[code].dropna().plot(ax=ax, label="5 day EMA")
        ax.set_title(f"{industry_code} close and EMA: {code}")
        ax.legend(loc="best")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)

    return {
        "stocks": stocks,
        "tracked_codes": chosen_codes,
        "close_wide": close_wide,
        "tracked_close_wide": tracked_close_wide,
        "ema20_wide": ema20_wide,
        "ema5_wide": ema5_wide,
        "figure": fig,
        "output_path": output_path,
    }


# %%
RUN_EMA_EXPERIMENT = False
if RUN_EMA_EXPERIMENT:
    result = run_industry_ema_experiment()
    print(f"tracked_codes={result['tracked_codes']}")
    print(result["tracked_close_wide"].tail())
    print(f"saved_plot={result['output_path']}")

# %%
def run_get_bars_fq_experiment(
    *,
    end_dt: str = "2025-12-31 14:50:00",
    test_codes: list[str] | None = None,
    count: int = 20,
):
    """
    对比 get_bars 在不同 fq_ref_date 下的返回结构与价格差异：
    1) fq_ref_date=None（不复权）
    2) fq_ref_date=end_dt 当日（前复权到当日）
    3) fq_ref_date=历史锚点（定点复权）
    """
    default_codes = test_codes or ["000998.XSHE", "600108.XSHG"]
    with jq_client.session() as jq_api:
        bars_none = jq_api.get_bars(
            default_codes,
            end_dt=end_dt,
            count=count,
            unit="1d",
            fields=["date", "close"],
            include_now=False,
            fq_ref_date=None,
            df=True,
        )
        bars_pre_today = jq_api.get_bars(
            default_codes,
            end_dt=end_dt,
            count=count,
            unit="1d",
            fields=["date", "close"],
            include_now=False,
            fq_ref_date=end_dt[:10],  # 前复权到 end_dt 所在日
            df=True,
        )
        bars_fixed = jq_api.get_bars(
            default_codes,
            end_dt=end_dt,
            count=count,
            unit="1d",
            fields=["date", "close"],
            include_now=False,
            fq_ref_date="2018-01-02",  # 定点复权锚点
            df=True,
        )

    def _to_wide_groupby_check(df, securities):
        """
        写法1（兼容性优先）：
        用 groupby(level='bar_idx').nunique() 校验 bar_idx->date 唯一性，再 unstack close。
        """
        if isinstance(securities, str):
            securities = [securities]
        if df is None or len(df) == 0:
            raise ValueError("empty get_bars result")

        if isinstance(df.index, pd.MultiIndex):
            if not {"date", "close"}.issubset(df.columns):
                raise ValueError("missing required columns {'date','close'} in multi-security result")
            multi = df.rename_axis(index=["code", "bar_idx"])
            # 一次 groupby 同时拿到 date 唯一数与首个 date，避免重复 groupby
            date_stats = multi["date"].groupby(level="bar_idx").agg(["nunique", "first"])
            if (date_stats["nunique"] != 1).any():
                raise ValueError("bar_idx maps to multiple dates; cannot safely align wide table")
            out = multi["close"].unstack(0).reindex(columns=securities)
            out.index = pd.to_datetime(date_stats["first"])
            out.index.name = "date"
            return out.sort_index()

        if "close" not in df.columns:
            raise ValueError("missing required column 'close' in single-security result")
        if "date" in df.columns:
            index = pd.to_datetime(df["date"])
        else:
            index = pd.to_datetime(df.index)
        out = pd.DataFrame({securities[0]: df["close"].to_numpy()}, index=index)
        out.index.name = "date"
        return out.sort_index()

    wide_none = _to_wide_groupby_check(bars_none, default_codes)
    wide_pre_today = _to_wide_groupby_check(bars_pre_today, default_codes)
    wide_fixed = _to_wide_groupby_check(bars_fixed, default_codes)

    print("\n=== get_bars 返回结构 ===")
    print("bars_none index names:", bars_none.index.names)
    print("bars_none columns:", list(bars_none.columns))
    print("bars_none head:")
    print(bars_none.head(5))

    print("\n=== close 对比（尾部5行）=== ")
    print("[fq_ref_date=None]")
    print(wide_none.tail(5))
    print("\n[fq_ref_date=end_dt_date]")
    print(wide_pre_today.tail(5))
    print("\n[fq_ref_date=2018-01-02]")
    print(wide_fixed.tail(5))

    # 同日同标的的复权差异倍率，帮助直观看区别
    ratio_pre = (wide_pre_today / wide_none).replace([float("inf"), float("-inf")], float("nan"))
    ratio_fixed = (wide_fixed / wide_none).replace([float("inf"), float("-inf")], float("nan"))
    print("\n=== 与不复权相比的倍率（尾部5行）===")
    print("[pre_today / none]")
    print(ratio_pre.tail(5))
    print("\n[fixed / none]")
    print(ratio_fixed.tail(5))

    return {
        "bars_none": bars_none,
        "bars_pre_today": bars_pre_today,
        "bars_fixed": bars_fixed,
        "wide_none": wide_none,
        "wide_pre_today": wide_pre_today,
        "wide_fixed": wide_fixed,
        "ratio_pre": ratio_pre,
        "ratio_fixed": ratio_fixed,
    }

RUN_GET_BARS_FQ_EXPERIMENT = False
if RUN_GET_BARS_FQ_EXPERIMENT:
    bars_result = run_get_bars_fq_experiment()
    print(bars_result["bars_none"].head())
    print(bars_result["bars_pre_today"].head())
    print(bars_result["bars_fixed"].head())
    print(bars_result["wide_none"].tail())
    print(bars_result["wide_pre_today"].tail())
    print(bars_result["wide_fixed"].tail())
    print(bars_result["ratio_pre"].tail())
    print(bars_result["ratio_fixed"].tail())

# %%
def run_bonus_filter_may_branch_test(
    *,
    jq_api,
    stock_list: list[str],
    end_date: str = "2025-09-20",
):
    if not stock_list:
        raise ValueError("stock_list is empty")

    end_dt = pd.to_datetime(end_date).to_pydatetime()
    start_date = datetime.datetime(year=end_dt.year, month=1, day=1)

    # 单步调试：先拉 code.in_(stock_list) 的基础集合，再逐步过滤并打印数量变化
    q = jq_api.query(
        jq_api.finance.STK_XR_XD.code,
        jq_api.finance.STK_XR_XD.company_name,
        jq_api.finance.STK_XR_XD.board_plan_pub_date,
        jq_api.finance.STK_XR_XD.implementation_pub_date,
        jq_api.finance.STK_XR_XD.bonus_amount_rmb,
        jq_api.finance.STK_XR_XD.bonus_ratio_rmb,
    ).filter(
        jq_api.finance.STK_XR_XD.code.in_(stock_list),
        jq_api.finance.STK_XR_XD.board_plan_pub_date > start_date,
        jq_api.finance.STK_XR_XD.implementation_pub_date <= end_dt,
        jq_api.finance.STK_XR_XD.bonus_ratio_rmb > 0,
    )
    expected_bonus_df = jq_api.finance.run_query(q)
    if expected_bonus_df is None or expected_bonus_df.empty:
        print("expected_bonus_df is empty after SQL filters")
        return pd.DataFrame()

    print(
        f"[debug] sql-filtered rows={len(expected_bonus_df)}, "
        f"unique_codes={expected_bonus_df['code'].nunique()}"
    )

    bonus_list = expected_bonus_df["code"].unique().tolist()
    # 与策略对齐：优先使用 history；本地 jqdatasdk 无 history 时回退到 get_price
    if hasattr(jq_api, "history"):
        price_df = jq_api.history(
            1,
            unit="1d",
            field="close",
            security_list=bonus_list,
            df=True,
            skip_paused=False,
            fq="pre",
        )
        price_df = price_df.T
        price_df.rename(columns={price_df.columns[0]: "Close_now"}, inplace=True)
        price_df["code"] = price_df.index
    else:
        px = jq_api.get_price(
            bonus_list,
            end_date=end_dt,
            count=1,
            frequency="daily",
            fields=["close"],
            panel=False,
            fq="pre",
        )
        if px is None or px.empty:
            print("price data is empty")
            return pd.DataFrame()
        price_df = (
            px[["code", "close"]]
            .rename(columns={"close": "Close_now"})
            .drop_duplicates(subset=["code"])
        )
    merged = pd.merge(expected_bonus_df, price_df, on=("code",), how="left")
    merged["bonus_ratio"] = merged["bonus_ratio_rmb"] / merged["Close_now"]
    merged = merged.sort_values(by="bonus_ratio", ascending=False)
    print(merged[["code", "company_name", "bonus_ratio_rmb", "Close_now", "bonus_ratio"]].head(20))
    return merged


RUN_BONUS_MAY_BRANCH_TEST = True
if RUN_BONUS_MAY_BRANCH_TEST:
    with jq_client.session() as jq_api:
        sample_stocks = jq_api.get_index_stocks("399101.XSHE", date="2025-05-20")[:80]
        bonus_debug_df = run_bonus_filter_may_branch_test(
            jq_api=jq_api,
            stock_list=sample_stocks,
            end_date="2025-09-20",
        )

# %%
