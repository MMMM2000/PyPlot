"""Reusable transition-point estimates from tangent-line intersections."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LinearSegmentFit:
    slope: float
    intercept: float
    start_x: float
    end_x: float
    rmse: float


@dataclass(frozen=True)
class TangentTransitionFit:
    start_x: float
    finish_x: float
    before: LinearSegmentFit
    transition: LinearSegmentFit
    after: LinearSegmentFit
    rmse: float

    @property
    def transition_slope(self) -> float:
        return self.transition.slope


def fit_tangent_transition(
    x_values: Iterable[float],
    y_values: Iterable[float],
    *,
    min_segment_points: int = 8,
    max_points: int = 240,
) -> TangentTransitionFit | None:
    """Fit before/transition/after tangents and return their intersections."""

    x, y = _clean_xy(x_values, y_values)
    if len(x) < min_segment_points * 3:
        return None
    if len(x) > max_points:
        indices = np.linspace(0, len(x) - 1, max_points).round().astype(int)
        x = x[indices]
        y = y[indices]

    min_points = max(2, int(min_segment_points))
    best: tuple[float, int, int, LinearSegmentFit, LinearSegmentFit, LinearSegmentFit] | None = None
    n = len(x)
    for left_end in range(min_points, n - (min_points * 2) + 1):
        for right_start in range(left_end + min_points, n - min_points + 1):
            before = _fit_segment(x[:left_end], y[:left_end])
            transition = _fit_segment(x[left_end:right_start], y[left_end:right_start])
            after = _fit_segment(x[right_start:], y[right_start:])
            if before is None or transition is None or after is None:
                continue
            slope_gain = abs(transition.slope) - max(abs(before.slope), abs(after.slope))
            if slope_gain <= 0.0:
                continue
            total_rmse = _combined_rmse(
                before.rmse,
                left_end,
                transition.rmse,
                right_start - left_end,
                after.rmse,
                n - right_start,
            )
            score = total_rmse / max(slope_gain, 1e-12)
            if best is None or score < best[0]:
                best = (score, left_end, right_start, before, transition, after)
    if best is None:
        return None

    _score, _left_end, _right_start, before, transition, after = best
    start_x = _line_intersection_x(before, transition)
    finish_x = _line_intersection_x(transition, after)
    if start_x is None or finish_x is None:
        return None
    lower = float(np.min(x))
    upper = float(np.max(x))
    start_x = min(max(start_x, lower), upper)
    finish_x = min(max(finish_x, lower), upper)
    if finish_x < start_x:
        start_x, finish_x = finish_x, start_x
    return TangentTransitionFit(
        start_x=start_x,
        finish_x=finish_x,
        before=before,
        transition=transition,
        after=after,
        rmse=_combined_rmse(
            before.rmse,
            _left_end,
            transition.rmse,
            _right_start - _left_end,
            after.rmse,
            len(x) - _right_start,
        ),
    )


def estimate_temperature_transition_points(frame: pd.DataFrame) -> dict[str, float]:
    """Estimate As/Af/Ms/Mf from VSM-like temperature, signal, field data."""

    if frame.empty or not {"temperature", "signal"}.issubset(frame.columns):
        return {}
    points: dict[str, float] = {}
    best_by_direction: dict[str, tuple[int, TangentTransitionFit]] = {}
    for direction, segment in _temperature_segments(frame):
        fit = fit_tangent_transition(
            segment["temperature"],
            segment["signal"],
            min_segment_points=8,
        )
        if fit is None:
            continue
        count = int(len(segment.index))
        current = best_by_direction.get(direction)
        if current is None or count > current[0]:
            best_by_direction[direction] = (count, fit)

    heating = best_by_direction.get("up")
    if heating is not None:
        fit = heating[1]
        points["As"] = float(fit.start_x)
        points["Af"] = float(fit.finish_x)
    cooling = best_by_direction.get("down")
    if cooling is not None:
        fit = cooling[1]
        points["Mf"] = float(fit.start_x)
        points["Ms"] = float(fit.finish_x)
    return points


def _clean_xy(x_values: Iterable[float], y_values: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(list(x_values), dtype=float)
    y = np.asarray(list(y_values), dtype=float)
    if x.shape != y.shape:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) == 0:
        return x, y
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    unique_x: list[float] = []
    unique_y: list[float] = []
    for value in np.unique(x):
        value_mask = x == value
        unique_x.append(float(value))
        unique_y.append(float(np.mean(y[value_mask])))
    return np.asarray(unique_x, dtype=float), np.asarray(unique_y, dtype=float)


def _temperature_segments(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    working = frame.copy()
    working["temperature"] = pd.to_numeric(working["temperature"], errors="coerce")
    working["signal"] = pd.to_numeric(working["signal"], errors="coerce")
    working = working.dropna(subset=["temperature", "signal"])
    if working.empty:
        return []

    segments: list[tuple[str, pd.DataFrame]] = []
    group_columns = [column for column in ("field", "section_index") if column in working.columns]
    grouped = working.groupby(group_columns, sort=False) if group_columns else [(None, working)]
    for _key, group in grouped:
        raw = group.reset_index(drop=True)
        if len(raw.index) < 2:
            continue
        delta = float(raw["temperature"].iloc[-1] - raw["temperature"].iloc[0])
        if math.isclose(delta, 0.0, abs_tol=1e-9):
            continue
        direction = "up" if delta > 0 else "down"
        segments.append((direction, raw.sort_values("temperature").reset_index(drop=True)))
    return segments


def _fit_segment(x: np.ndarray, y: np.ndarray) -> LinearSegmentFit | None:
    if len(x) < 2 or len(np.unique(x)) < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    fitted = (slope * x) + intercept
    rmse = float(np.sqrt(np.mean((y - fitted) ** 2)))
    if not all(math.isfinite(float(value)) for value in (slope, intercept, rmse)):
        return None
    return LinearSegmentFit(
        slope=float(slope),
        intercept=float(intercept),
        start_x=float(x[0]),
        end_x=float(x[-1]),
        rmse=rmse,
    )


def _line_intersection_x(left: LinearSegmentFit, right: LinearSegmentFit) -> float | None:
    denominator = left.slope - right.slope
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        return None
    value = (right.intercept - left.intercept) / denominator
    return float(value) if math.isfinite(float(value)) else None


def _combined_rmse(
    first_rmse: float,
    first_count: int,
    second_rmse: float,
    second_count: int,
    third_rmse: float,
    third_count: int,
) -> float:
    total = first_count + second_count + third_count
    if total <= 0:
        return float("inf")
    sse = (
        (first_rmse**2 * first_count)
        + (second_rmse**2 * second_count)
        + (third_rmse**2 * third_count)
    )
    return float(math.sqrt(sse / total))
