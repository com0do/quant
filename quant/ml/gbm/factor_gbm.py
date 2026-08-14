"""GBM 因子聚合：训练 / 保存 / 加载 / 推理。

- 特征列：与 simple_3factor 保持一致，方便同基准对比
  ['pe_ratio', 'pb_ratio', 'roe', 'ret_20', 'ret_60', 'vol_20']
- 标签：前向 N 日超额收益（个股相对当日横截面均值的 forward return）
- 模型：LightGBM 回归器；训练/验证按时间切分（不做随机 shuffle）
- 保存路径默认 `data/models/gbm_multifactor.txt`

lightgbm 未安装时：`train_model` 抛出明确错误，`predict_scores` 在 strategy 层降级为中性分。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


FEATURE_COLS: list[str] = ["pe_ratio", "pb_ratio", "roe", "ret_20", "ret_60", "vol_20"]

DEFAULT_MODEL_PATH = "data/models/gbm_multifactor.txt"
DEFAULT_METRICS_PATH = "data/models/gbm_multifactor.metrics.json"


@dataclass
class GbmTrainConfig:
    db_path: str
    start_date: str = "2023-01-01"
    end_date: str = "2025-06-30"
    valid_ratio: float = 0.2
    forward_days: int = 5
    min_universe_size: int = 200
    sample_every_n_days: int = 5
    num_leaves: int = 31
    learning_rate: float = 0.05
    num_boost_round: int = 400
    early_stopping_rounds: int = 30
    seed: int = 42


def _load_prices(db_path: str, start_date: str, end_date: str) -> pd.DataFrame:
    """从 prices_daily 拉取全表 close/paused，做前向收益标签所需的全部准备。"""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT date, code, close, paused
            FROM prices_daily
            WHERE date >= ? AND date <= ?
            """,
            conn,
            params=(start_date, end_date),
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["paused"] = pd.to_numeric(df["paused"], errors="coerce").fillna(0).astype(int)
    return df


def _add_price_features(prices: pd.DataFrame) -> pd.DataFrame:
    """在价格宽表上加 ret_20/ret_60/vol_20；输入必须按 (code, date) 排好。"""
    if prices.empty:
        return prices
    p = prices.copy()
    grp = p.groupby("code")["close"]
    p["ret_20"] = grp.pct_change(20)
    p["ret_60"] = grp.pct_change(60)
    p["vol_20"] = grp.pct_change().rolling(20).std(ddof=0).reset_index(level=0, drop=True)
    return p


def _add_forward_label(prices: pd.DataFrame, forward_days: int) -> pd.DataFrame:
    """加 fwd_ret（前向 forward_days 天收益率）。"""
    p = prices.copy()
    p["fwd_ret"] = p.groupby("code")["close"].pct_change(forward_days).shift(-forward_days)
    return p


def _load_factors(db_path: str, start_date: str, end_date: str) -> pd.DataFrame:
    """从 factors_snapshot 拉基本面因子。"""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT date, code, pe_ratio, pb_ratio, roe
            FROM factors_snapshot
            WHERE date >= ? AND date <= ?
            """,
            conn,
            params=(start_date, end_date),
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for c in ["pe_ratio", "pb_ratio", "roe"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def _cross_section_demean(df: pd.DataFrame, col: str) -> pd.Series:
    """按 date 组内做去均值。"""
    return df[col] - df.groupby("date")[col].transform("mean")


def build_training_frame(cfg: GbmTrainConfig) -> pd.DataFrame:
    """构造训练样本表：每一行 (date, code, features..., fwd_ret_demeaned)。"""
    prices = _load_prices(cfg.db_path, cfg.start_date, cfg.end_date)
    if prices.empty:
        raise RuntimeError(f"[gbm] no prices in {cfg.db_path} for {cfg.start_date}~{cfg.end_date}")

    prices = _add_price_features(prices)
    prices = _add_forward_label(prices, cfg.forward_days)

    factors = _load_factors(cfg.db_path, cfg.start_date, cfg.end_date)
    if factors.empty:
        raise RuntimeError(f"[gbm] no factors_snapshot rows in {cfg.db_path}")

    df = prices.merge(factors, on=["date", "code"], how="inner")
    df = df[df["paused"] == 0].copy()
    df = df.dropna(subset=FEATURE_COLS + ["fwd_ret"])

    if cfg.sample_every_n_days > 1:
        all_dates = sorted(df["date"].unique())
        keep = set(all_dates[:: cfg.sample_every_n_days])
        df = df[df["date"].isin(keep)]

    counts = df.groupby("date")["code"].count()
    keep_dates = counts[counts >= cfg.min_universe_size].index
    df = df[df["date"].isin(keep_dates)]

    df["label"] = _cross_section_demean(df, "fwd_ret")

    return df.reset_index(drop=True)


def _split_by_date(df: pd.DataFrame, valid_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = np.sort(df["date"].unique())
    if len(dates) < 4:
        raise RuntimeError("[gbm] not enough distinct dates for train/valid split")
    cutoff_idx = int(len(dates) * (1.0 - valid_ratio))
    cutoff = dates[max(1, cutoff_idx)]
    train = df[df["date"] < cutoff].copy()
    valid = df[df["date"] >= cutoff].copy()
    return train, valid


def train_model(df: pd.DataFrame, cfg: GbmTrainConfig, model_path: str = DEFAULT_MODEL_PATH) -> dict:
    """用 LightGBM 训练一个回归模型；返回 metrics + 保存路径。"""
    try:
        import lightgbm as lgb
    except ImportError as e:
        raise RuntimeError(
            "lightgbm not installed. Run: uv add lightgbm  (or `uv sync` after adding it to pyproject.toml)."
        ) from e

    train, valid = _split_by_date(df, cfg.valid_ratio)

    x_tr = train[FEATURE_COLS].values.astype(np.float64)
    y_tr = train["label"].values.astype(np.float64)
    x_va = valid[FEATURE_COLS].values.astype(np.float64)
    y_va = valid["label"].values.astype(np.float64)

    dtrain = lgb.Dataset(x_tr, y_tr, feature_name=FEATURE_COLS, free_raw_data=False)
    dvalid = lgb.Dataset(x_va, y_va, feature_name=FEATURE_COLS, reference=dtrain, free_raw_data=False)

    params: dict[str, Any] = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": cfg.num_leaves,
        "learning_rate": cfg.learning_rate,
        "min_data_in_leaf": 100,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 5,
        "seed": cfg.seed,
        "verbose": -1,
    }

    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[dtrain, dvalid],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(cfg.early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    )

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(model_path)

    pred_va = booster.predict(x_va)
    rank_ic = _rank_ic_by_date(valid.assign(pred=pred_va))
    metrics = {
        "n_samples_total": int(len(df)),
        "n_train": int(len(train)),
        "n_valid": int(len(valid)),
        "best_iter": int(booster.best_iteration or cfg.num_boost_round),
        "valid_rank_ic_mean": float(rank_ic.mean()),
        "valid_rank_ic_std": float(rank_ic.std(ddof=0)),
        "valid_rank_ic_ir": float(rank_ic.mean() / (rank_ic.std(ddof=0) + 1e-9)),
        "feature_importance": dict(zip(FEATURE_COLS, booster.feature_importance().tolist())),
        "model_path": model_path,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "config": vars(cfg),
    }
    Path(DEFAULT_METRICS_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(DEFAULT_METRICS_PATH).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def _rank_ic_by_date(df: pd.DataFrame) -> pd.Series:
    """按日截面 rank IC。"""
    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 5:
            return np.nan
        return float(g["pred"].rank().corr(g["label"].rank()))
    ic = df.groupby("date").apply(_ic)
    return ic.dropna()


def load_model(model_path: str = DEFAULT_MODEL_PATH):
    """加载 LightGBM Booster。"""
    try:
        import lightgbm as lgb
    except ImportError as e:
        raise RuntimeError("lightgbm not installed") from e
    if not Path(model_path).exists():
        raise FileNotFoundError(f"gbm model not found: {model_path}")
    return lgb.Booster(model_file=model_path)


def predict_scores(model, features: pd.DataFrame) -> pd.Series:
    """给定 [code + FEATURE_COLS] 的 DataFrame，返回按 code 索引的分数 Series。"""
    if features is None or features.empty:
        return pd.Series(dtype=float)
    x = features[FEATURE_COLS].values.astype(np.float64)
    y = model.predict(x)
    return pd.Series(y, index=features["code"].astype(str).values).sort_values(ascending=False)


def run_train(
    db_path: str | None = None,
    start_date: str = "2023-01-01",
    end_date: str = "2025-06-30",
    forward_days: int = 5,
    model_path: str = DEFAULT_MODEL_PATH,
    config_path: str | None = None,
) -> dict:
    """CLI 入口。db_path 未指定时，从 config 里读 `data.runtime_csi1000_db_path`。"""
    if db_path is None:
        from quant.config import load_config
        cfg = load_config(config_path=config_path)
        db_path = str(getattr(cfg.data, "runtime_csi1000_db_path", cfg.data.sqlite_db_path))

    tcfg = GbmTrainConfig(
        db_path=db_path,
        start_date=start_date,
        end_date=end_date,
        forward_days=forward_days,
    )
    print(f"[gbm-train] db={tcfg.db_path} start={tcfg.start_date} end={tcfg.end_date} fwd={tcfg.forward_days}")
    df = build_training_frame(tcfg)
    print(f"[gbm-train] samples={len(df)} distinct_dates={df['date'].nunique()}")
    metrics = train_model(df, tcfg, model_path=model_path)
    print(f"[gbm-train] done. valid rank IC={metrics['valid_rank_ic_mean']:.4f} "
          f"IR={metrics['valid_rank_ic_ir']:.4f} best_iter={metrics['best_iter']}")
    print(f"[gbm-train] model saved to {model_path}")
    return metrics
