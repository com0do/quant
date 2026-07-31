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


class AdaptiveRegimeMidtermStrategy:
    """
    自适应中线策略：
    - 热点趋势市: 提高趋势/强业绩权重，避免系统性踏空
    - 震荡回撤市: 回到低热度 + 低位反弹 + 业绩支撑
    """

    name = "adaptive_regime_midterm"

    def _regime(self, x: pd.DataFrame) -> str:
        close = pd.to_numeric(x.get("close"), errors="coerce")
        ma20 = pd.to_numeric(x.get("ma_20"), errors="coerce")
        ret20 = pd.to_numeric(x.get("ret_20"), errors="coerce")
        breadth = float((close > ma20).mean()) if ma20.notna().any() else 0.0
        med20 = float(ret20.median()) if ret20.notna().any() else 0.0
        mean20 = float(ret20.mean()) if ret20.notna().any() else 0.0
        if breadth >= 0.62 and med20 >= 0.03 and mean20 >= 0.04:
            return "trend_hot"
        return "defensive_rebound"

    def _low_heat(self, x: pd.DataFrame) -> pd.Series:
        sec = _load_sector_map()
        if sec.empty:
            return -zscore(x.get("turnover_ratio")) - 0.4 * zscore(x.get("ret_20"))
        x2 = x.merge(sec, on="code", how="left")
        ind_heat = x2.groupby("industry", dropna=False).agg(
            heat_ret20=("ret_20", "mean"),
            heat_turn=("turnover_ratio", "mean"),
        )
        ind_heat["heat"] = zscore(ind_heat["heat_ret20"]) + zscore(ind_heat["heat_turn"])
        x2 = x2.merge(ind_heat["heat"], left_on="industry", right_index=True, how="left")
        return -pd.to_numeric(x2["heat"], errors="coerce").fillna(0.0)

    def _low_position_rebound_score(self, x: pd.DataFrame) -> pd.Series:
        quality = 0.9 * zscore(x.get("roe")) - 0.45 * zscore(x.get("pe_ratio")) - 0.35 * zscore(x.get("pb_ratio"))
        dd = pd.to_numeric(x.get("dd_120"), errors="coerce").fillna(0.0)
        low_pos = (-dd).clip(lower=0.0)
        rebound = (
            (pd.to_numeric(x.get("ma_5"), errors="coerce") > pd.to_numeric(x.get("ma_10"), errors="coerce")).astype(float)
            + (pd.to_numeric(x.get("close"), errors="coerce") > pd.to_numeric(x.get("ma_5"), errors="coerce")).astype(float)
            + (pd.to_numeric(x.get("ret_5"), errors="coerce") > 0).astype(float)
        ) / 3.0
        hot_penalty = (dd > -0.04).astype(float) + (pd.to_numeric(x.get("ret_60"), errors="coerce").fillna(0.0) > 0.55).astype(float)
        low_heat = self._low_heat(x)
        ma20 = pd.to_numeric(x.get("ma_20"), errors="coerce")
        ma60 = pd.to_numeric(x.get("ma_60"), errors="coerce")
        ret20 = pd.to_numeric(x.get("ret_20"), errors="coerce")
        ret5 = pd.to_numeric(x.get("ret_5"), errors="coerce")
        vr20 = pd.to_numeric(x.get("vol_ratio20"), errors="coerce")
        macd_dif = pd.to_numeric(x.get("macd_dif"), errors="coerce")
        macd_dea = pd.to_numeric(x.get("macd_dea"), errors="coerce")
        macd_hist = pd.to_numeric(x.get("macd_hist"), errors="coerce")
        close = pd.to_numeric(x.get("close"), errors="coerce")
        ma50 = pd.to_numeric(x.get("ma_50"), errors="coerce")
        ma200 = pd.to_numeric(x.get("ma_200"), errors="coerce")
        top_break_risk = pd.to_numeric(x.get("top_break_risk"), errors="coerce").fillna(0.0)
        support_break_down = pd.to_numeric(x.get("support_break_down"), errors="coerce").fillna(0.0)
        uptrend_break_down = pd.to_numeric(x.get("uptrend_break_down"), errors="coerce").fillna(0.0)
        downtrend_break_up = pd.to_numeric(x.get("downtrend_break_up"), errors="coerce").fillna(0.0)
        inverse_top_break_signal = pd.to_numeric(x.get("inverse_top_break_signal"), errors="coerce").fillna(0.0)
        normal_pullback_signal = pd.to_numeric(x.get("normal_pullback_signal"), errors="coerce").fillna(0.0)
        trend_resume_signal = pd.to_numeric(x.get("trend_resume_signal"), errors="coerce").fillna(0.0)
        abnormal_climax_reversal = pd.to_numeric(x.get("abnormal_climax_reversal"), errors="coerce").fillna(0.0)
        downtrend = ((ma20 < ma60) | (ret20 < 0)).fillna(False)
        trap_rebound = (downtrend & (ret5 > 0) & (vr20 > 1.8) & (macd_hist < 0) & (macd_dif < macd_dea)).astype(float)
        long_trend_penalty = (close < ma200).astype(float).fillna(0.0)
        mid_trend_penalty = ((close < ma50) & (close >= ma200)).astype(float).fillna(0.0)
        return (
            1.00 * quality
            + 0.95 * zscore(low_pos)
            + 0.75 * rebound
            + 0.60 * low_heat
            + 0.40 * downtrend_break_up
            + 0.50 * inverse_top_break_signal
            + 0.30 * normal_pullback_signal
            + 0.35 * trend_resume_signal
            - 0.70 * hot_penalty
            - 0.85 * trap_rebound
            - 0.80 * support_break_down
            - 0.70 * uptrend_break_down
            - 0.85 * top_break_risk
            - 1.00 * abnormal_climax_reversal
            - 0.95 * long_trend_penalty
            - 0.25 * mid_trend_penalty
        )

    def _high_position_pullback_rebound_score(self, x: pd.DataFrame) -> pd.Series:
        """
        捕捉“高位主升中的回调反弹”:
        - 中期强势 (ret60, ma20>ma60)
        - 近端回调 (dd_120 不太深, 且 ret_5 先弱后转强)
        - 回弹确认 (close>ma10, ma5>=ma10)
        """
        quality = 0.8 * zscore(x.get("roe")) - 0.3 * zscore(x.get("pe_ratio"))
        trend_core = 0.85 * zscore(x.get("ret_60")) + 0.45 * zscore(x.get("ret_20"))
        trend_structure = (
            (pd.to_numeric(x.get("ma_20"), errors="coerce") > pd.to_numeric(x.get("ma_60"), errors="coerce")).astype(float)
            + (pd.to_numeric(x.get("close"), errors="coerce") > pd.to_numeric(x.get("ma_20"), errors="coerce")).astype(float)
        ) / 2.0
        dd = pd.to_numeric(x.get("dd_120"), errors="coerce").fillna(0.0)
        pullback_zone = ((dd <= -0.03) & (dd >= -0.18)).astype(float)
        rebound_confirm = (
            (pd.to_numeric(x.get("close"), errors="coerce") > pd.to_numeric(x.get("ma_10"), errors="coerce")).astype(float)
            + (pd.to_numeric(x.get("ma_5"), errors="coerce") >= pd.to_numeric(x.get("ma_10"), errors="coerce")).astype(float)
            + (pd.to_numeric(x.get("ret_5"), errors="coerce") > 0).astype(float)
        ) / 3.0
        vr20 = pd.to_numeric(x.get("vol_ratio20"), errors="coerce")
        macd_dif = pd.to_numeric(x.get("macd_dif"), errors="coerce")
        macd_dea = pd.to_numeric(x.get("macd_dea"), errors="coerce")
        macd_hist = pd.to_numeric(x.get("macd_hist"), errors="coerce")
        top_break_risk = pd.to_numeric(x.get("top_break_risk"), errors="coerce").fillna(0.0)
        support_break_down = pd.to_numeric(x.get("support_break_down"), errors="coerce").fillna(0.0)
        uptrend_break_down = pd.to_numeric(x.get("uptrend_break_down"), errors="coerce").fillna(0.0)
        normal_pullback_signal = pd.to_numeric(x.get("normal_pullback_signal"), errors="coerce").fillna(0.0)
        trend_resume_signal = pd.to_numeric(x.get("trend_resume_signal"), errors="coerce").fillna(0.0)
        abnormal_climax_reversal = pd.to_numeric(x.get("abnormal_climax_reversal"), errors="coerce").fillna(0.0)
        # 进攻层同样规避“弱MACD下的异常放量反抽”
        trap_rebound = ((pd.to_numeric(x.get("ret_20"), errors="coerce") < 0) & (pd.to_numeric(x.get("ret_5"), errors="coerce") > 0) & (vr20 > 1.8) & (macd_hist < 0) & (macd_dif < macd_dea)).astype(float)
        trend_bonus = ((macd_dif > macd_dea) & (macd_hist > 0) & (vr20 >= 1.0) & (vr20 <= 1.8)).astype(float)
        overheat_penalty = (pd.to_numeric(x.get("ret_20"), errors="coerce").fillna(0.0) > 0.35).astype(float)
        return (
            0.7 * quality
            + 1.15 * trend_core
            + 0.7 * trend_structure
            + 0.55 * pullback_zone
            + 0.65 * rebound_confirm
            + 0.20 * trend_bonus
            + 0.25 * normal_pullback_signal
            + 0.35 * trend_resume_signal
            - 0.65 * trap_rebound
            - 0.65 * support_break_down
            - 0.55 * uptrend_break_down
            - 0.70 * top_break_risk
            - 1.00 * abnormal_climax_reversal
            - 0.6 * overheat_penalty
        )

    def buy_scores(self, ctx: StrategyContext) -> pd.Series:
        if ctx.market_features is None or ctx.market_features.empty:
            return pd.Series(dtype=float)
        x = ctx.market_features.copy()
        if ctx.factors is not None and not ctx.factors.empty:
            x = x.merge(
                ctx.factors[["code", "roe", "pe_ratio", "pb_ratio", "turnover_ratio"]].drop_duplicates(subset=["code"], keep="last"),
                on="code",
                how="left",
            )
        if x.empty:
            return pd.Series(dtype=float)

        regime = self._regime(x)
        low_score = self._low_position_rebound_score(x)
        high_score = self._high_position_pullback_rebound_score(x)
        if regime == "trend_hot":
            # 热点趋势时，提升“高位回调反弹”权重，避免系统性踏空
            score = 0.35 * low_score + 0.65 * high_score
        else:
            score = 0.70 * low_score + 0.30 * high_score
        out = pd.Series(score.values, index=x["code"].astype(str))
        return out.sort_values(ascending=False)

    def sell_scores(self, ctx: StrategyContext) -> pd.Series:
        if not ctx.held_codes:
            return pd.Series(dtype=float)
        b = self.buy_scores(ctx)
        if b.empty:
            return pd.Series(dtype=float)
        held = b.reindex(ctx.held_codes).fillna(b.min() if len(b) else 0.0)
        urgency = (held.max() - held) / (held.max() - held.min() + 1e-9)
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
            urgency = urgency.add(trend_break.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(0.8 * top_break.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(0.7 * support_break.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(0.6 * uptrend_break.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(0.6 * ma200_break.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(1.0 * abnormal_rev.reindex(urgency.index).fillna(0.0), fill_value=0.0)
            urgency = urgency.add(-0.6 * normal_pullback.reindex(urgency.index).fillna(0.0), fill_value=0.0)
        return urgency.sort_values(ascending=False)
