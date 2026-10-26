"""Signed endpoint strain changes for one measured heating/cooling sweep."""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import pandas as pd


def split_sweep_branches(group: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, bool, str]:
    """Keep the high-current hold on heating; require a sustained return ramp."""
    empty = group.iloc[0:0].copy()
    if group.empty or "current_mA" not in group:
        return empty, empty, False, "No usable current sweep was recorded."
    current = pd.to_numeric(group["current_mA"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if not current.notna().any():
        return empty, empty, False, "No usable current values were recorded."
    peak = int(current.fillna(-math.inf).to_numpy().argmax())
    span = float(current.max() - current.min())
    tolerance = max(0.02, span * 0.001)
    # Include a contiguous turnaround hold, without jumping to a later cycle.
    while peak + 1 < len(current) and current.iloc[peak + 1] >= current.iloc[peak] - tolerance:
        if abs(current.iloc[peak + 1] - current.max()) > tolerance:
            break
        peak += 1
    heating, cooling = group.iloc[:peak + 1].copy(), group.iloc[peak:].copy()
    descending = current.iloc[peak:].dropna()
    differences = descending.diff().dropna()
    falling_steps = int((differences < -tolerance).sum())
    tested_steps = int((differences.abs() > tolerance).sum())
    drop = float(descending.iloc[0] - descending.iloc[-1]) if len(descending) >= 2 else 0.0
    recorded = bool(
        len(descending) >= 6 and falling_steps >= 4 and tested_steps > 0
        and falling_steps / tested_steps >= 0.55 and drop >= max(1.0, span * 0.05)
    )
    reason = (
        f"Sustained cooling ramp detected: {falling_steps} falling-current steps and {drop:.3g} mA total decrease."
        if recorded else
        "No sustained cooling ramp was recorded; short or abrupt current drops are treated as heating termination or wire failure."
    )
    return heating, cooling, recorded, reason


def cycle_strain_metrics(group: pd.DataFrame, length_mm: pd.Series | None = None) -> dict[str, object]:
    """Use one reference for both legs; never infer room-temperature recovery.

    With calibrated lengths the reference is the minimum length of this paired
    sweep, matching the existing TMA strain plot. Without that calibration,
    report differences of recorded strain and leave physical lengths unknown.
    """
    heating, cooling, cooling_recorded, reason = split_sweep_branches(group)
    metrics: dict[str, object] = {
        "version": 1, "method": "signed_endpoint_difference",
        "reference_method": "unavailable", "reference_length_mm": None,
        "heating_strain_pct": None, "cooling_strain_pct": None, "residual_strain_pct": None,
        "heating_recorded": False, "cooling_recorded": cooling_recorded,
        "cooling_reason": reason, "endpoint": "recorded cooling endpoint",
    }
    if heating.empty:
        return metrics

    def finite(series: pd.Series, position: int) -> float | None:
        try:
            value = float(series.iloc[position])
        except (ValueError, TypeError, IndexError):
            return None
        return value if math.isfinite(value) else None

    if length_mm is not None:
        length = pd.to_numeric(length_mm.reindex(group.index), errors="coerce")
        reference = finite(length, int(length.fillna(math.inf).to_numpy().argmin()))
        if reference is None or reference <= 0 or not np.isfinite(length).all():
            metrics["reference_method"] = "invalid_length_trace"
            return metrics
        values = length / reference * 100.0
        metrics.update(reference_method="per_target_minimum_length", reference_length_mm=reference)
    else:
        length = None
        if "strain_pct" not in group:
            return metrics
        values = pd.to_numeric(group["strain_pct"], errors="coerce")
        metrics["reference_method"] = "recorded_strain_pct"
    turn = len(heating) - 1
    start, hot, end = finite(values, 0), finite(values, turn), finite(values, -1)
    current = pd.to_numeric(group["current_mA"], errors="coerce")
    start_current, hot_current = finite(current, 0), finite(current, turn)
    heating_recorded = bool(
        turn > 0 and start_current is not None and hot_current is not None
        and hot_current > start_current + max(0.02, abs(hot_current - start_current) * 0.001)
    )
    metrics["heating_recorded"] = heating_recorded
    if heating_recorded and start is not None and hot is not None:
        metrics["heating_strain_pct"] = hot - start
    if cooling_recorded and hot is not None and end is not None:
        metrics["cooling_strain_pct"] = end - hot
        if heating_recorded and start is not None:
            metrics["residual_strain_pct"] = end - start
    for name, position, available in (("start", 0, True), ("turnaround", turn, True), ("cooling_end", -1, cooling_recorded)):
        metrics[f"{name}_current_mA"] = finite(current, position) if available else None
        metrics[f"{name}_length_mm"] = finite(length, position) if available and length is not None else None
        if "elapsed_s" in group:
            metrics[f"{name}_elapsed_s"] = finite(group["elapsed_s"], position) if available else None
    end_time, turn_time = metrics.get("cooling_end_elapsed_s"), metrics.get("turnaround_elapsed_s")
    metrics["cooling_duration_s"] = (
        end_time - turn_time if isinstance(end_time, (int, float)) and isinstance(turn_time, (int, float)) and end_time >= turn_time else None
    )
    return metrics


def apply_cooling_review_override(metrics: Mapping[str, object], override: object) -> dict[str, object]:
    """Respect a reviewer's absent-cooling decision without inventing endpoints."""
    result = dict(metrics)
    if override is False:
        result.update(cooling_review_override=False, cooling_recorded=False,
                      cooling_reason="Reviewer marked the cooling branch as absent.")
        for key in ("cooling_strain_pct", "residual_strain_pct", "cooling_duration_s",
                    "cooling_end_current_mA", "cooling_end_length_mm", "cooling_end_elapsed_s"):
            result[key] = None
    return result


def clean_cycle_strain_metrics(value: object) -> dict[str, object]:
    """Keep versioned, finite derived values in portable JSON records."""
    if not isinstance(value, Mapping) or value.get("version") != 1:
        return {}
    cleaned: dict[str, object] = {"version": 1}
    for key in ("method", "reference_method", "cooling_reason", "endpoint"):
        if isinstance(value.get(key), str):
            cleaned[key] = value[key]
    for key in ("heating_recorded", "cooling_recorded", "cooling_review_override"):
        if isinstance(value.get(key), bool):
            cleaned[key] = value[key]
    fields = ["reference_length_mm", "heating_strain_pct", "cooling_strain_pct", "residual_strain_pct", "cooling_duration_s"]
    fields += [f"{endpoint}_{quantity}" for endpoint in ("start", "turnaround", "cooling_end") for quantity in ("current_mA", "length_mm", "elapsed_s")]
    for key in fields:
        item = value.get(key)
        cleaned[key] = float(item) if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) else None
    return cleaned
