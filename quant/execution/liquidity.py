from __future__ import annotations


def allow_by_adv(
    slice_notional: float,
    adv_notional: float,
    max_ratio_per_slice: float = 0.05,
    min_adv_notional: float = 5_000_000.0,
) -> bool:
    if adv_notional <= 0:
        return False
    if adv_notional < min_adv_notional:
        return False
    return (slice_notional / adv_notional) <= max_ratio_per_slice
