"""GBM factor aggregation.

Uses LightGBM to learn a non-linear ranker that replaces the linear weighting in simple_3factor.
- Training: `quant.ml.gbm.factor_gbm.run_train(...)` (also available via `main.py --mode gbm-train`)
- Inference: `quant.stock_strategy.gbm_multifactor.GbmMultifactorStrategy`

lightgbm is an optional dependency: when the model is missing the strategy degrades to a neutral score (the main flow is not interrupted).
"""

from quant.ml.gbm.factor_gbm import (
    FEATURE_COLS,
    build_training_frame,
    load_model,
    predict_scores,
    run_train,
    train_model,
)

__all__ = [
    "FEATURE_COLS",
    "build_training_frame",
    "train_model",
    "load_model",
    "predict_scores",
    "run_train",
]
