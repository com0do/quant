from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from quant.stock_strategy.base import StrategyContext
from quant.stock_strategy.tech_features import zscore


@lru_cache(maxsize=1)
def _load_sector_map() -> pd.DataFrame:
    path = Path("data/stock_exposure.csv")
    if not path.exists():
        return pd.DataFrame(columns=["code", "industry"])
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["code", "industry"])
    if "code" not in df.columns:
        return pd.DataFrame(columns=["code", "industry"])
    ind_col = "industry" if "industry" in df.columns else ("industry_name" if "industry_name" in df.columns else None)
    if ind_col is None:
        return pd.DataFrame(columns=["code", "industry"])
    out = df[["code", ind_col]].copy()
    out.columns = ["code", "industry"]
    out["code"] = out["code"].astype(str)
    out["industry"] = out["industry"].astype(str)
    return out.dropna().drop_duplicates(subset=["code"], keep="last")


class LowHeatQualityReboundStrategy:
    """
    中线逻辑：
    1) 业绩支撑（ROE/估值）
    2) 低位（相对120日高点有显著回撤）
    3) 出现反弹确认（MA5>MA10 + 短期收益转正）
    4) 偏向低热度（低热行业/低换手拥挤）
    """

    name = "low_heat_quality_rebound"

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        if ctx.market_features is None or ctx.market_features.empty:
            return pd.Series(dtype=float)
        if ctx.factors is None or ctx.factors.empty:
            return pd.Series(dtype=float)

        x = ctx.market_features.copy()
        fac = ctx.factors.copy()
        x = x.merge(
            fac[["code", "roe", "pe_ratio", "pb_ratio", "turnover_ratio"]].drop_duplicates(subset=["code"], keep="last"),
            on="code",
            how="left",
        )
        if x.empty:
            return pd.Series(dtype=float)

        # 业绩 + 估值（低PE/PB + 高ROE）
        quality = 0.9 * zscore(x["roe"]) - 0.45 * zscore(x["pe_ratio"]) - 0.35 * zscore(x["pb_ratio"])

        # 低位：离120日高点回撤越大越好，避免追高新高股
        dd = pd.to_numeric(x["dd_120"], errors="coerce").fillna(0.0)
        low_position = (-dd).clip(lower=0.0)
        not_new_high_penalty = (dd > -0.05).astype(float) + (pd.to_numeric(x["ret_60"], errors="coerce").fillna(0.0) > 0.50).astype(float)

        # 反弹确认：短均线上穿 + 短周期收益转正
        rebound = (
            (pd.to_numeric(x["ma_5"], errors="coerce") > pd.to_numeric(x["ma_10"], errors="coerce")).astype(float)
            + (pd.to_numeric(x["close"], errors="coerce") > pd.to_numeric(x["ma_5"], errors="coerce")).astype(float)
            + (pd.to_numeric(x["ret_5"], errors="coerce") > 0).astype(float)
        ) / 3.0

        # 基础量价 + MACD 过滤：
        # 下跌趋势中“放量反抽”更偏诱多，降低评分；反之对温和放量+MACD转强给小幅加分
        ma20 = pd.to_numeric(x.get("ma_20"), errors="coerce")
        ma60 = pd.to_numeric(x.get("ma_60"), errors="coerce")
        ma50 = pd.to_numeric(x.get("ma_50"), errors="coerce")
        ma200 = pd.to_numeric(x.get("ma_200"), errors="coerce")
        close = pd.to_numeric(x.get("close"), errors="coerce")
        ret20 = pd.to_numeric(x.get("ret_20"), errors="coerce")
        ret5 = pd.to_numeric(x.get("ret_5"), errors="coerce")
        vr20 = pd.to_numeric(x.get("vol_ratio20"), errors="coerce")
        macd_dif = pd.to_numeric(x.get("macd_dif"), errors="coerce")
        macd_dea = pd.to_numeric(x.get("macd_dea"), errors="coerce")
        macd_hist = pd.to_numeric(x.get("macd_hist"), errors="coerce")
        top_break_risk = pd.to_numeric(x.get("top_break_risk"), errors="coerce").fillna(0.0)
        support_break_down = pd.to_numeric(x.get("support_break_down"), errors="coerce").fillna(0.0)
        uptrend_break_down = pd.to_numeric(x.get("uptrend_break_down"), errors="coerce").fillna(0.0)
        downtrend_break_up = pd.to_numeric(x.get("downtrend_break_up"), errors="coerce").fillna(0.0)
        inverse_top_break_signal = pd.to_numeric(x.get("inverse_top_break_signal"), errors="coerce").fillna(0.0)
        normal_pullback_signal = pd.to_numeric(x.get("normal_pullback_signal"), errors="coerce").fillna(0.0)
        trend_resume_signal = pd.to_numeric(x.get("trend_resume_signal"), errors="coerce").fillna(0.0)
        abnormal_climax_reversal = pd.to_numeric(x.get("abnormal_climax_reversal"), errors="coerce").fillna(0.0)

        downtrend = ((ma20 < ma60) | (ret20 < 0)).fillna(False)
        weak_macd = ((macd_hist < 0) & (macd_dif < macd_dea)).fillna(False)
        trap_rebound = (downtrend & weak_macd & (ret5 > 0) & (vr20 > 1.8)).astype(float)
        healthy_rebound = ((~downtrend) & (macd_dif > macd_dea) & (macd_hist > 0) & (vr20 >= 1.0) & (vr20 <= 1.8)).astype(float)
        long_trend_penalty = (close < ma200).astype(float).fillna(0.0)
        mid_trend_penalty = ((close < ma50) & (close >= ma200)).astype(float).fillna(0.0)

        # 低热度：优先低热行业（有行业映射时），否则退化为低换手/非拥挤
        sec = _load_sector_map()
        if not sec.empty:
            x2 = x.merge(sec, on="code", how="left")
            ind_heat = x2.groupby("industry", dropna=False).agg(
                heat_ret20=("ret_20", "mean"),
                heat_turn=("turnover_ratio", "mean"),
            )
            ind_heat["heat"] = zscore(ind_heat["heat_ret20"]) + zscore(ind_heat["heat_turn"])
            x2 = x2.merge(ind_heat["heat"], left_on="industry", right_index=True, how="left")
            low_heat = -pd.to_numeric(x2["heat"], errors="coerce").fillna(0.0)
        else:
            low_heat = -zscore(x["turnover_ratio"]) - 0.5 * zscore(x["ret_20"])

        score = (
            1.0 * quality
            + 0.9 * zscore(low_position)
            + 0.7 * rebound
            + 0.6 * low_heat
            + 0.25 * healthy_rebound
            + 0.45 * downtrend_break_up
            + 0.55 * inverse_top_break_signal
            + 0.35 * normal_pullback_signal
            + 0.45 * trend_resume_signal
            - 0.9 * trap_rebound
            - 0.85 * support_break_down
            - 0.75 * uptrend_break_down
            - 0.9 * top_break_risk
            - 1.05 * abnormal_climax_reversal
            - 1.0 * long_trend_penalty
            - 0.3 * mid_trend_penalty
            - 0.8 * not_new_high_penalty
        )
        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        if not ctx.held_codes:
            return pd.Series(dtype=float)
        b = self.buy_scores(ctx)
        if b.empty:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        # Buy score 低 + 破位，卖出紧迫度升高
        base = (held.max() - held) / (held.max() - held.min() + 1e-9)
        if ctx.market_features is not None and not ctx.market_features.empty:
            mf = ctx.market_features.set_index("code")
            trend_break = (
                (pd.to_numeric(mf.get("close"), errors="coerce") < pd.to_numeric(mf.get("ma_20"), errors="coerce")).astype(float)
                + (pd.to_numeric(mf.get("ret_20"), errors="coerce") < 0).astype(float)
            ) / 2.0
            top_break = pd.to_numeric(mf.get("top_break_risk"), errors="coerce").fillna(0.0)
            support_break = pd.to_numeric(mf.get("support_break_down"), errors="coerce").fillna(0.0)
            uptrend_break = pd.to_numeric(mf.get("uptrend_break_down"), errors="coerce").fillna(0.0)
            normal_pullback = pd.to_numeric(mf.get("normal_pullback_signal"), errors="coerce").fillna(0.0)
            abnormal_rev = pd.to_numeric(mf.get("abnormal_climax_reversal"), errors="coerce").fillna(0.0)
            ma200_break = (
                pd.to_numeric(mf.get("close"), errors="coerce")
                < pd.to_numeric(mf.get("ma_200"), errors="coerce")
            ).astype(float)
            base = base.add(trend_break.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(0.8 * top_break.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(0.8 * support_break.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(0.7 * uptrend_break.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(0.6 * ma200_break.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(1.0 * abnormal_rev.reindex(base.index).fillna(0.0), fill_value=0.0)
            base = base.add(-0.6 * normal_pullback.reindex(base.index).fillna(0.0), fill_value=0.0)
        return base.sort_values(ascending=False)
