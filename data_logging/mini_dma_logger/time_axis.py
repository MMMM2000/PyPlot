"""Shared elapsed-time display units for live and saved TMA plots."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TimeAxisDisplay:
    divisor_s: float
    label: str


def time_axis_display(max_elapsed_s: float) -> TimeAxisDisplay:
    """Choose a readable unit while keeping stored elapsed time in seconds."""

    finite_elapsed_s = (
        max(0.0, float(max_elapsed_s))
        if math.isfinite(max_elapsed_s)
        else 0.0
    )
    if finite_elapsed_s >= 3600.0:
        return TimeAxisDisplay(divisor_s=3600.0, label="Time (h)")
    if finite_elapsed_s >= 60.0:
        return TimeAxisDisplay(divisor_s=60.0, label="Time (min)")
    return TimeAxisDisplay(divisor_s=1.0, label="Time (s)")
