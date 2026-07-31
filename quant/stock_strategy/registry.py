from __future__ import annotations

from quant.stock_strategy.csi1000_enhanced import CSI1000EnhancedStrategy
from quant.stock_strategy.adaptive_regime_midterm import AdaptiveRegimeMidtermStrategy
from quant.stock_strategy.dow_trend import DowTrendStrategy
from quant.stock_strategy.jq_multifactor_clone import JqMultifactorCloneStrategy
from quant.stock_strategy.low_heat_quality_rebound import LowHeatQualityReboundStrategy
from quant.stock_strategy.price_only_cross_section import PriceOnlyCrossSectionStrategy
from quant.stock_strategy.value_activity_dow import ValueActivityDowStrategy
from quant.stock_strategy.simple_3factor import Simple3FactorStrategy, MomentumQualityStrategy


def build_strategy(name: str):
    name = str(name)
    if name == "price_only_cross_section":
        return PriceOnlyCrossSectionStrategy()
    if name == "csi1000_enhanced":
        return CSI1000EnhancedStrategy()
    if name == "adaptive_regime_midterm":
        return AdaptiveRegimeMidtermStrategy()
    if name == "value_activity_dow":
        return ValueActivityDowStrategy()
    if name == "dow_trend":
        return DowTrendStrategy()
    if name == "jq_multifactor_clone":
        return JqMultifactorCloneStrategy()
    if name == "low_heat_quality_rebound":
        return LowHeatQualityReboundStrategy()
    if name == "simple_3factor":
        return Simple3FactorStrategy()
    if name == "mom_quality":
        return MomentumQualityStrategy()
    raise KeyError(f"unknown strategy: {name}")
