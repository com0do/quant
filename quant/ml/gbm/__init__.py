"""GBM 因子聚合。

用 LightGBM 学一个非线性排序器，替代 simple_3factor 里的线性加权。
- 训练：`quant.ml.gbm.factor_gbm.run_train(...)`（也可通过 `main.py --mode gbm-train`）
- 推理：`quant.stock_strategy.gbm_multifactor.GbmMultifactorStrategy`

lightgbm 是可选依赖：strategy 会在模型缺失时降级为中性分（不打断主链路）。
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
