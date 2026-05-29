from __future__ import annotations

import math
from dataclasses import dataclass


def quantize_floor(value: float, resolution: float) -> float:
    resolution = max(1e-9, abs(float(resolution)))
    value = max(0.0, float(value))
    return math.floor((value / resolution) + 1e-9) * resolution


@dataclass
class RateLimitedCurrentRamp:
    current_mA: float
    target_mA: float
    rate_mA_s: float
    resolution_mA: float
    max_step_mA: float
    last_step_s: float

    def __init__(
        self,
        *,
        initial_mA: float,
        target_mA: float,
        rate_mA_s: float,
        resolution_mA: float,
        max_step_mA: float,
        now_s: float,
    ) -> None:
        self.resolution_mA = max(0.001, abs(float(resolution_mA)))
        self.max_step_mA = max(self.resolution_mA, quantize_floor(max_step_mA, self.resolution_mA))
        self.current_mA = quantize_floor(initial_mA, self.resolution_mA)
        self.target_mA = quantize_floor(target_mA, self.resolution_mA)
        self.rate_mA_s = max(self.resolution_mA, quantize_floor(rate_mA_s, self.resolution_mA))
        self.last_step_s = float(now_s)

    @property
    def is_complete(self) -> bool:
        return abs(self.current_mA - self.target_mA) < (self.resolution_mA / 2.0)

    def update_target(self, *, target_mA: float, rate_mA_s: float | None = None, now_s: float) -> None:
        self.target_mA = quantize_floor(target_mA, self.resolution_mA)
        if rate_mA_s is not None:
            self.rate_mA_s = max(self.resolution_mA, quantize_floor(rate_mA_s, self.resolution_mA))
        self.last_step_s = min(self.last_step_s, float(now_s))

    def next_setpoint(self, *, now_s: float) -> float | None:
        if self.is_complete:
            return None
        direction = 1.0 if self.target_mA > self.current_mA else -1.0
        elapsed_s = max(0.0, float(now_s) - self.last_step_s)
        step_budget = quantize_floor(self.rate_mA_s * elapsed_s, self.resolution_mA)
        step_mA = min(self.max_step_mA, step_budget)
        if step_mA < self.resolution_mA:
            return None

        remaining_mA = abs(self.target_mA - self.current_mA)
        step_mA = min(step_mA, remaining_mA)
        next_mA = self.current_mA + direction * step_mA
        if direction > 0:
            next_mA = min(next_mA, self.target_mA)
        else:
            next_mA = max(next_mA, self.target_mA)
        self.current_mA = max(0.0, next_mA)
        self.last_step_s = float(now_s)
        return self.current_mA
