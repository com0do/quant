from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlicePlan:
    quantity: int
    offset_sec: int


def twap_slices(total_qty: int, slices: int, interval_sec: int) -> list[SlicePlan]:
    slices = max(1, int(slices))
    base = total_qty // slices
    rem = total_qty % slices
    out: list[SlicePlan] = []
    for i in range(slices):
        q = base + (1 if i < rem else 0)
        if q <= 0:
            continue
        out.append(SlicePlan(quantity=q, offset_sec=i * max(1, interval_sec)))
    return out


def vwap_slices(total_qty: int, profile: list[float], interval_sec: int) -> list[SlicePlan]:
    if not profile:
        return twap_slices(total_qty, 1, interval_sec)
    s = sum(max(0.0, x) for x in profile)
    if s <= 1e-12:
        return twap_slices(total_qty, len(profile), interval_sec)
    raw = [total_qty * max(0.0, x) / s for x in profile]
    q = [int(v) for v in raw]
    while sum(q) < total_qty:
        i = max(range(len(raw)), key=lambda k: raw[k] - q[k])
        q[i] += 1
    out: list[SlicePlan] = []
    for i, qty in enumerate(q):
        if qty > 0:
            out.append(SlicePlan(quantity=qty, offset_sec=i * max(1, interval_sec)))
    return out
