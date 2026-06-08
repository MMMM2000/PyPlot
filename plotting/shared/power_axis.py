from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd
from matplotlib.axes import Axes


def power_mw_from_current_resistance(
    current_mA: Sequence[float],
    resistance_ohm: Sequence[float],
) -> np.ndarray:
    """Return electrical power in mW for current in mA and resistance in ohm."""

    current = np.asarray(current_mA, dtype=float)
    resistance = np.asarray(resistance_ohm, dtype=float)
    return (current * current * resistance) / 1000.0


def add_power_top_axis(
    ax: Axes,
    current_mA: Sequence[float],
    resistance_ohm: Sequence[float],
    *,
    label: str = "Power [mW]",
    power_scale: float = 1.0,
    label_size: float | None = None,
    tick_size: float | None = None,
) -> Axes | None:
    """Add a top X axis whose tick labels show P = I^2 R at bottom-axis ticks."""

    tick_positions = np.asarray(ax.get_xticks(), dtype=float)
    ticks_and_labels = power_tick_positions_and_labels(
        current_mA,
        resistance_ohm,
        tick_positions=tick_positions,
        power_scale=power_scale,
    )
    if ticks_and_labels is None:
        return None
    ticks, labels = ticks_and_labels

    top_ax = ax.twiny()
    top_ax.set_xticks(ticks)
    top_ax.set_xticklabels(labels)
    top_ax.set_xlim(ax.get_xlim())
    ax.callbacks.connect("xlim_changed", lambda primary: top_ax.set_xlim(primary.get_xlim()))
    top_ax.set_xlabel(label)
    if label_size is not None:
        top_ax.xaxis.label.set_fontsize(label_size)
    if tick_size is not None:
        top_ax.tick_params(axis="x", labelsize=tick_size)
    return top_ax


def power_tick_positions_and_labels(
    current_mA: Sequence[float],
    resistance_ohm: Sequence[float],
    *,
    tick_positions: Sequence[float],
    power_scale: float = 1.0,
) -> tuple[np.ndarray, list[str]] | None:
    """Return current-axis tick positions and power labels for P = I^2 R."""

    current = np.asarray(current_mA, dtype=float)
    resistance = np.asarray(resistance_ohm, dtype=float)
    mask = np.isfinite(current) & np.isfinite(resistance)
    if not mask.any():
        return None

    try:
        scale = float(power_scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not math.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    frame = pd.DataFrame(
        {
            "current_mA": current[mask],
            "power_mW": power_mw_from_current_resistance(current[mask], resistance[mask])
            * scale,
        }
    )
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        return None
    # Multiple curves can share a current value; the median gives a stable axis label.
    grouped = (
        frame.groupby("current_mA", sort=True, as_index=False)["power_mW"]
        .median()
        .sort_values("current_mA", kind="stable")
    )
    if grouped.empty:
        return None
    x_values = grouped["current_mA"].to_numpy(dtype=float)
    p_values = grouped["power_mW"].to_numpy(dtype=float)
    unique = np.unique(x_values)
    if unique.size < 2:
        return None

    ticks = np.asarray(tick_positions, dtype=float)
    xmin = float(np.nanmin(x_values))
    xmax = float(np.nanmax(x_values))
    valid_ticks: list[float] = []
    labels: list[str] = []
    for tick in ticks:
        if not math.isfinite(float(tick)) or tick < xmin or tick > xmax:
            continue
        valid_ticks.append(float(tick))
        power = float(np.interp(tick, x_values, p_values))
        labels.append(_format_power_tick(power))
    if len(valid_ticks) < 2:
        return None
    return np.asarray(valid_ticks, dtype=float), labels


def _format_power_tick(value_mW: float) -> str:
    if not math.isfinite(value_mW):
        return ""
    if abs(value_mW) >= 1000:
        return f"{value_mW:.0f}"
    if abs(value_mW) >= 100:
        return f"{value_mW:.0f}"
    if abs(value_mW) >= 10:
        return f"{value_mW:.1f}".rstrip("0").rstrip(".")
    if abs(value_mW) >= 1:
        return f"{value_mW:.2f}".rstrip("0").rstrip(".")
    return f"{value_mW:.3f}".rstrip("0").rstrip(".")
