from __future__ import annotations

import csv
import json
import os
import re
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Tuple, cast

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .burnthrough import trim_burnthrough_glitch
from plotting.shared.backends import wants_matplotlib, wants_origin
from plotting.shared.utils import save_figure, show_plots, schedule_origin_release
from plotting.shared.origin import (
    origin_session,
    hide_origin_workbook,
    set_origin_axis_title as shared_set_origin_axis_title,
    set_origin_graph_title as shared_set_origin_graph_title,
)
from plotting.shared.power_axis import add_power_top_axis
from plotting.shared.readability import apply_readability_fonts, apply_readability
from plotting.shared.transition_analysis import (
    LinearSegmentFit,
    TangentTransitionFit,
    fit_tangent_transition,
)
from plotting.shared.toolkit import format_annealing_title

# Defaults
OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAVE_FORMAT = "png"
PNG_DPI = 1200
BACKEND = "matplotlib"
IMPROVE_READABILITY = False
SHOW_LEGEND = True
LEGEND_SIZE = 12
LEGEND_ORIENTATION = "auto"
LEGEND_SHOW_SYMBOLS = False
LEGEND_SYMBOL_SIZE = 8.0
TICK_SIZE = 10
AXIS_LABEL_SIZE = 12
TITLE_SIZE = 16
SHOW_TICK_LABELS = True
SHOW_AXIS_LABELS = True
SHOW_TITLE = True
INCREASING_COLORS = ["#dc2626", "#f97316", "#ea580c", "#ef4444"]
DECREASING_COLORS = ["#2563eb", "#0ea5e9", "#1d4ed8", "#06b6d4"]

# Relax figure count warning for batch plotting inside PyPlot tabs
plt.rcParams["figure.max_open_warning"] = 0

# Origin export is intentionally fixed to direction-separated traces.
ORIGIN_MODES: Tuple[str, ...] = ("directional",)
ORIGIN_MODE: str = ORIGIN_MODES[0]


_SUBSCRIPT_PATTERN = re.compile(r"([A-Z][a-z])(\d+)")
_CURRENT_TARGET_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*mA\b", re.IGNORECASE)


@dataclass(frozen=True)
class AnnealingTransitionSummary:
    as_current_mA: float | None = None
    af_current_mA: float | None = None
    ms_current_mA: float | None = None
    mf_current_mA: float | None = None
    loop_index: int | None = None


@dataclass(frozen=True)
class _AnnealingTransitionCandidate:
    fit: TangentTransitionFit
    linear_rmse: float
    score: float
    count: int


def _format_origin_annotation(text: str) -> str:
    """Return Origin rich-text markup for the sample description."""

    formatted = text.replace("_", "/")

    def _sub(match: re.Match[str]) -> str:
        element, digits = match.groups()
        return f"{element}\\-({digits})"

    return _SUBSCRIPT_PATTERN.sub(_sub, formatted)


def _escape_ltalk_text(text: str) -> str:
    return str(text).replace('"', '""')


def _expected_current_limit_mA() -> float:
    return 1000.0


def _target_current_from_path(path: str) -> float | None:
    match = _CURRENT_TARGET_PATTERN.search(Path(path).name)
    if not match:
        return None
    try:
        return float(match.group("value"))
    except Exception:
        return None


def _infer_current_scale_to_mA(path: str, raw_currents: pd.Series) -> float:
    finite = pd.to_numeric(raw_currents, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return 1.0
    abs_values = finite.abs()
    raw_max = float(abs_values.max())
    if not math.isfinite(raw_max) or raw_max <= 0.0:
        return 1.0

    target_mA = _target_current_from_path(path)
    if target_mA is not None and math.isfinite(target_mA) and target_mA > 0.0:
        candidates = (
            (1.0, raw_max),
            (1000.0, raw_max * 1000.0),
        )
        best_scale = min(candidates, key=lambda item: abs(item[1] - target_mA))[0]
        return float(best_scale)

    return 1000.0 if raw_max <= 1.2 else 1.0


def resolve_measurement_path(path: str | Path) -> Path:
    """Resolve either a legacy data file or a logger run folder."""

    source = Path(path)
    if source.is_dir():
        metadata_path = source / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = None
        if isinstance(metadata, dict) and metadata.get("data_file"):
            measurement = source / str(metadata["data_file"])
            if measurement.is_file():
                return measurement
        for name in ("measurement.csv", "measurement.txt"):
            measurement = source / name
            if measurement.is_file():
                return measurement
        raise ValueError(f"{source}: run folder has no measurement.csv or measurement.txt")
    return source


def measurement_display_name(path: str | Path) -> str:
    """Return the human run name for files from either storage layout."""

    source = resolve_measurement_path(path)
    if source.name.casefold() in {"measurement.txt", "measurement.csv"}:
        return source.parent.name
    return source.stem


def load_file(path: str | Path) -> pd.DataFrame:
    """Load current annealing tri-column file: I(A) V(V) R(Ohm).

    Returns a DataFrame with I_mA and R_Ohm columns.
    """
    source = resolve_measurement_path(path)

    if source.name.casefold() == "measurement.csv":
        session_frame = pd.read_csv(source)
        required = {"measured_current_mA", "resistance_ohm"}
        if required.issubset(session_frame.columns):
            frame = pd.DataFrame(
                {
                    "I_mA": pd.to_numeric(
                        session_frame["measured_current_mA"], errors="coerce"
                    ),
                    "R_Ohm": pd.to_numeric(
                        session_frame["resistance_ohm"], errors="coerce"
                    ),
                }
            )
            frame = frame.replace([np.inf, -np.inf], np.nan)
            frame = frame.dropna(subset=["I_mA", "R_Ohm"]).reset_index(drop=True)
            frame = frame.loc[frame["I_mA"] != 0].reset_index(drop=True)
            if frame.empty:
                raise ValueError(f"{source}: no usable samples after filtering zeros")
            currents = frame["I_mA"].to_numpy(dtype=float)
            resistances = frame["R_Ohm"].to_numpy(dtype=float)
            trimmed_currents, trimmed_resistances = trim_burnthrough_glitch(
                currents, resistances
            )
            if trimmed_currents.shape[0] != currents.shape[0]:
                frame = frame.iloc[: trimmed_currents.shape[0]].copy()
                frame["I_mA"] = trimmed_currents
                frame["R_Ohm"] = trimmed_resistances
            return frame[["I_mA", "R_Ohm"]]

    def _read(sep: str | None) -> pd.DataFrame:
        return pd.read_csv(
            source,
            sep=sep,
            engine="python",
            header=None,
            comment="#",
            dtype=str,
        )

    try:
        df = _read(None)
    except (csv.Error, pd.errors.ParserError):
        df = _read(r"\s+")
    else:
        if df.shape[1] > 3:
            df = _read(r"\s+")
    if df.shape[1] < 3:
        raise ValueError(f"{source}: expected at least 3 columns (I, V, R)")
    df = df.iloc[:, :3].copy()
    df.columns = ["I_A", "V_V", "R_Ohm"]

    def _to_numeric(series: pd.Series) -> pd.Series:
        cleaned = (
            series.astype(str)
            .str.replace("\u2212", "-", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
        return pd.to_numeric(cleaned, errors="coerce")

    df["I_A"] = _to_numeric(df["I_A"])
    df["V_V"] = _to_numeric(df["V_V"])
    df["R_Ohm"] = _to_numeric(df["R_Ohm"])
    df = df.dropna(subset=["I_A", "R_Ohm"]).reset_index(drop=True)
    while len(df) > 1 and float(df.loc[0, "R_Ohm"]) <= 0.0:
        df = df.iloc[1:].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{source}: no valid samples after parsing")
    scale_hint = (
        str(source.parent)
        if source.name.casefold() == "measurement.txt"
        else str(source)
    )
    scale_to_mA = _infer_current_scale_to_mA(scale_hint, df["I_A"])
    if scale_to_mA == 1000.0:
        df["I_mA"] = df["I_A"] * 1e3
    else:
        df["I_mA"] = df["I_A"]
    df["I_A"] = df["I_mA"] / 1e3
    max_current_mA = float(df["I_mA"].abs().max()) if not df["I_mA"].empty else 0.0
    if math.isfinite(max_current_mA) and max_current_mA > (_expected_current_limit_mA() + 1e-6):
        raise ValueError(
            f"{source}: current exceeds expected {_expected_current_limit_mA():.0f} mA ceiling after unit detection"
        )
    mask = (
        np.isfinite(df["I_mA"]) &
        np.isfinite(df["R_Ohm"]) &
        (df["I_mA"] != 0)
    )
    df = df.loc[mask].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{source}: no usable samples after filtering zeros")
    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    trimmed_currents, trimmed_resistances = trim_burnthrough_glitch(currents, resistances)
    if trimmed_currents is not currents:
        # NumPy may or may not return the original view; guard with a length check.
        if trimmed_currents.shape[0] != currents.shape[0]:
            df = df.iloc[: trimmed_currents.shape[0]].copy()
            df["I_mA"] = trimmed_currents
            df["R_Ohm"] = trimmed_resistances
    if len(df.index) > 3:
        tolerance = 0.6
        mask = np.ones(len(df), dtype=bool)
        values = df["I_mA"].to_numpy(dtype=float)
        for idx, value in enumerate(values):
            if not math.isfinite(value):
                continue
            if abs(value - 1.0) <= tolerance and idx < len(values) - 1:
                mask[idx] = False
        if not mask.all():
            df = df.loc[mask].reset_index(drop=True)
    return df[["I_mA", "R_Ohm"]]


def _direction_profile(currents: np.ndarray) -> Tuple[np.ndarray, List[Tuple[int, int, float]]]:
    """Return per-sample directions and contiguous segments."""

    count = currents.size
    if count == 0:
        return np.array([], dtype=float), []
    if count == 1:
        return np.array([1.0], dtype=float), [(0, 1, 1.0)]

    deltas = np.diff(currents)
    abs_deltas = np.abs(deltas[np.isfinite(deltas)])
    if abs_deltas.size:
        tolerance = max(float(np.quantile(abs_deltas, 0.25) * 0.5), 0.01)
    else:
        tolerance = 0.01

    signed = np.sign(deltas)
    signed[np.abs(deltas) <= tolerance] = 0.0
    direction = pd.Series(signed, index=range(1, count))
    direction = direction.replace(0.0, np.nan).reindex(range(count))
    if direction.isna().all():
        direction[:] = 1.0
    else:
        direction = direction.ffill()
        direction = direction.bfill()
    directions = direction.to_numpy(dtype=float)

    window = min(7, max(3, count // 20))
    smoothed = pd.Series(directions).rolling(window=window, center=True, min_periods=1).median()
    smoothed = smoothed.ffill().bfill()
    smoothed_values = smoothed.to_numpy(dtype=float)
    smoothed_values = np.where(smoothed_values >= 0, 1.0, -1.0)

    if not np.any(smoothed_values < 0):
        smoothed_values[:] = 1.0
    elif not np.any(smoothed_values > 0):
        smoothed_values[:] = -1.0

    segments: List[Tuple[int, int, float]] = []
    start = 0
    current_dir = smoothed_values[0]
    for idx in range(1, count):
        if smoothed_values[idx] != current_dir:
            segments.append((start, idx, current_dir))
            start = idx
            current_dir = smoothed_values[idx]
    segments.append((start, count, current_dir))
    return smoothed_values, segments


def summarize_transition_loops(df: pd.DataFrame) -> Tuple[AnnealingTransitionSummary, ...]:
    """Estimate annealing As/Af/Ms/Mf currents for each paired R(I) loop.

    Heating and cooling legs are assessed independently. A loop is emitted
    when either leg has a clear transition, so a missed heating fit does not
    suppress an otherwise well-defined cooling transition.
    """

    resistance_column = "R_Ohm" if "R_Ohm" in df.columns else "R_ohm"
    if df.empty or "I_mA" not in df.columns or resistance_column not in df.columns:
        return ()
    currents = pd.to_numeric(df["I_mA"], errors="coerce")
    resistances = pd.to_numeric(df[resistance_column], errors="coerce")
    working = pd.DataFrame({"I_mA": currents, "R_Ohm": resistances}).dropna()
    if len(working.index) < 48:
        return ()

    _directions, segments = _direction_profile(working["I_mA"].to_numpy(dtype=float))
    summaries: List[AnnealingTransitionSummary] = []
    loop_index = 0
    segment_count = len(segments)
    for segment_index, (start, end, direction) in enumerate(segments):
        if direction < 0:
            continue
        loop_index += 1
        heating_segment = working.iloc[start:end].copy()
        heating = _fit_heating_resistance_drop_segment(heating_segment)
        wrong_signed_heating = (
            _fit_annealing_transition_segment(
                heating_segment,
                transition_slope_sign=1,
            )
            if heating is None
            else None
        )
        maximum_current_mA = (
            heating.fit.finish_x
            if heating is not None
            else float(pd.to_numeric(heating_segment["I_mA"], errors="coerce").max())
        )
        cooling_candidates: List[_AnnealingTransitionCandidate] = []
        for next_start, next_end, next_direction in segments[segment_index + 1 : segment_count]:
            if next_direction >= 0:
                break
            candidate = _fit_cooling_resistance_increase_segment(
                working.iloc[next_start:next_end].copy(),
                max_current_mA=maximum_current_mA,
            )
            if candidate is None:
                continue
            if (
                candidate.fit.finish_x < maximum_current_mA
                and candidate.fit.start_x < maximum_current_mA
                and candidate.fit.transition.slope < 0.0
            ):
                cooling_candidates.append(candidate)
        cooling = (
            min(cooling_candidates, key=lambda candidate: candidate.score)
            if cooling_candidates
            else None
        )
        if heating is None and wrong_signed_heating is not None:
            continue
        if heating is None and cooling is None:
            continue
        summaries.append(
            AnnealingTransitionSummary(
                as_current_mA=heating.fit.start_x if heating is not None else None,
                af_current_mA=heating.fit.finish_x if heating is not None else None,
                mf_current_mA=cooling.fit.start_x if cooling is not None else None,
                ms_current_mA=cooling.fit.finish_x if cooling is not None else None,
                loop_index=loop_index,
            )
        )
    return tuple(summaries)


def summarize_transition_currents(df: pd.DataFrame) -> AnnealingTransitionSummary:
    """Estimate the first annealing As/Af/Ms/Mf current set from an R(I) trace."""

    summaries = summarize_transition_loops(df)
    if not summaries:
        return AnnealingTransitionSummary()
    first = summaries[0]
    return AnnealingTransitionSummary(
        as_current_mA=first.as_current_mA,
        af_current_mA=first.af_current_mA,
        ms_current_mA=first.ms_current_mA,
        mf_current_mA=first.mf_current_mA,
    )


def format_transition_summary(
    summary: AnnealingTransitionSummary,
    *,
    label: str | None = None,
) -> str:
    values = (
        ("As", summary.as_current_mA),
        ("Af", summary.af_current_mA),
        ("Ms", summary.ms_current_mA),
        ("Mf", summary.mf_current_mA),
    )
    if not any(value is not None for _name, value in values):
        return ""
    parts = [
        f"{name} {_format_compact_number(float(value), max_decimals=0)} mA"
        for name, value in values
        if value is not None
    ]
    prefix = f"{label}: " if label else ""
    return prefix + ", ".join(parts)


def format_transition_summaries(
    summaries: Tuple[AnnealingTransitionSummary, ...],
    *,
    label: str | None = None,
) -> Tuple[str, ...]:
    """Format one or more annealing loop summaries for review/table display."""

    lines: List[str] = []
    multiple = len(summaries) > 1
    for summary in summaries:
        loop_index = summary.loop_index
        line_label = label
        if loop_index is not None and (multiple or loop_index != 1):
            line_label = f"{label} loop {loop_index}" if label else f"Loop {loop_index}"
        text = format_transition_summary(summary, label=line_label)
        if text:
            lines.append(text)
    return tuple(lines)


def _fit_annealing_transition_segment(
    segment: pd.DataFrame,
    *,
    transition_slope_sign: int | None = None,
) -> _AnnealingTransitionCandidate | None:
    if len(segment.index) < 24:
        return None
    current = pd.to_numeric(segment["I_mA"], errors="coerce")
    resistance = pd.to_numeric(segment["R_Ohm"], errors="coerce")
    valid = current.notna() & resistance.notna()
    current_values = current.loc[valid].to_numpy(dtype=float)
    resistance_values = resistance.loc[valid].to_numpy(dtype=float)
    if len(current_values) < 24 or len(np.unique(current_values)) < 24:
        return None
    if transition_slope_sign is not None and transition_slope_sign < 0:
        if not _has_resistance_drop_candidate(current_values, resistance_values):
            return None

    fit = fit_tangent_transition(
        current_values,
        resistance_values,
        min_segment_points=8,
        max_points=72,
        min_slope_gain_ratio=2.5,
        min_transition_width_fraction=0.05,
        max_intersection_boundary_fraction=0.06,
        transition_slope_sign=transition_slope_sign,
    )
    if fit is None:
        return None

    y_range = float(np.nanmax(resistance_values) - np.nanmin(resistance_values))
    median_resistance = float(np.nanmedian(np.abs(resistance_values)))
    if median_resistance <= 0.0 or y_range / median_resistance < 0.05:
        return None
    linear_rmse = _linear_rmse(current_values, resistance_values)
    if not math.isfinite(linear_rmse) or linear_rmse <= 0.0:
        return None
    score = fit.rmse / linear_rmse
    if score > 0.7:
        return None
    return _AnnealingTransitionCandidate(
        fit=fit,
        linear_rmse=linear_rmse,
        score=score,
        count=len(current_values),
    )


def _fit_heating_resistance_drop_segment(
    segment: pd.DataFrame,
) -> _AnnealingTransitionCandidate | None:
    if len(segment.index) < 24:
        return None
    current = pd.to_numeric(segment["I_mA"], errors="coerce")
    resistance = pd.to_numeric(segment["R_Ohm"], errors="coerce")
    valid = current.notna() & resistance.notna()
    x = current.loc[valid].to_numpy(dtype=float)
    y = resistance.loc[valid].to_numpy(dtype=float)
    if len(x) < 24 or len(np.unique(x)) < 24:
        return None
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    scan_width = float(np.nanmax(x) - np.nanmin(x))
    if not math.isfinite(scan_width) or scan_width <= 0.0:
        return None

    smoothed = (
        pd.Series(y)
        .rolling(window=3, center=True, min_periods=1)
        .median()
        .to_numpy(dtype=float)
    )
    diffs = np.diff(smoothed)
    finite_diffs = np.abs(diffs[np.isfinite(diffs)])
    noise = float(np.nanmedian(finite_diffs)) if finite_diffs.size else 0.0
    median_resistance = float(np.nanmedian(np.abs(smoothed)))
    min_drop = max(0.004 * median_resistance, 1.1 * noise, 0.8)
    min_span = max(1.5, scan_width * 0.02)
    max_span = max(4.0, scan_width * 0.12)
    lower_limit = float(np.nanmin(x)) + max(5.0, scan_width * 0.08)
    candidates: List[tuple[float, int, int, float]] = []
    for start in range(2, len(x) - 5):
        if x[start] < lower_limit:
            continue
        if smoothed[start + 1] >= smoothed[start]:
            continue
        for end in range(start + 2, len(x) - 3):
            span = float(x[end] - x[start])
            if span < min_span:
                continue
            if span > max_span:
                break
            drop = float(smoothed[start] - smoothed[end])
            if drop < min_drop:
                continue
            after_end = min(len(x), end + 5)
            recovered = float(np.nanmax(smoothed[end + 1 : after_end])) if end + 1 < after_end else smoothed[end]
            if recovered < smoothed[end] + (0.35 * drop):
                continue
            score = float(x[start]) - (drop * 0.2) - (span * 0.02)
            candidates.append((score, start, end, drop))
    if not candidates:
        return None

    _score, start, end, drop = min(candidates, key=lambda item: item[0])
    before_start = max(0, start - 8)
    after_end = min(len(x), end + 9)
    before = _line_segment_fit(x[before_start : start + 1], y[before_start : start + 1])
    transition = _line_segment_fit(x[start : end + 1], y[start : end + 1])
    after = _line_segment_fit(x[end:after_end], y[end:after_end])
    if before is None or transition is None or after is None:
        return None
    if transition.slope >= 0.0:
        return None
    rmse = _combined_segment_rmse(
        (
            (before.rmse, start + 1 - before_start),
            (transition.rmse, end + 1 - start),
            (after.rmse, after_end - end),
        )
    )
    linear_rmse = _linear_rmse(x, y)
    fit = TangentTransitionFit(
        start_x=float(x[start]),
        finish_x=float(x[end]),
        before=before,
        transition=transition,
        after=after,
        rmse=rmse,
    )
    return _AnnealingTransitionCandidate(
        fit=fit,
        linear_rmse=linear_rmse,
        score=float(x[start]) - drop,
        count=len(x),
    )


def _fit_cooling_resistance_increase_segment(
    segment: pd.DataFrame,
    *,
    max_current_mA: float,
) -> _AnnealingTransitionCandidate | None:
    if len(segment.index) < 12:
        return None
    current = pd.to_numeric(segment["I_mA"], errors="coerce")
    resistance = pd.to_numeric(segment["R_Ohm"], errors="coerce")
    valid = current.notna() & resistance.notna()
    x = current.loc[valid].to_numpy(dtype=float)
    y = resistance.loc[valid].to_numpy(dtype=float)
    if len(x) < 12 or len(np.unique(x)) < 12:
        return None
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    eligible = x < float(max_current_mA)
    x = x[eligible]
    y = y[eligible]
    if len(x) < 10 or len(np.unique(x)) < 10:
        return None
    scan_width = float(np.nanmax(x) - np.nanmin(x))
    if not math.isfinite(scan_width) or scan_width <= 0.0:
        return None
    smoothed = (
        pd.Series(y)
        .rolling(window=3, center=True, min_periods=1)
        .median()
        .to_numpy(dtype=float)
    )
    diffs = np.diff(smoothed)
    finite_diffs = np.abs(diffs[np.isfinite(diffs)])
    noise = float(np.nanmedian(finite_diffs)) if finite_diffs.size else 0.0
    median_resistance = float(np.nanmedian(np.abs(smoothed)))
    min_rise = max(0.008 * median_resistance, 1.0 * noise, 0.8)
    min_span = max(0.8, scan_width * 0.04)
    max_span = max(3.5, scan_width * 0.25)
    low_current_limit = float(np.nanmin(x)) + max(8.0, scan_width * 0.60)
    candidates: List[tuple[float, int, int, float]] = []
    for start in range(2, len(x) - 5):
        if x[start] > low_current_limit:
            continue
        window_start = max(0, start - 2)
        window_end = min(len(smoothed), start + 3)
        if smoothed[start] < float(np.nanmax(smoothed[window_start:window_end])) - max(noise, 0.2):
            continue
        if smoothed[start + 1] >= smoothed[start]:
            continue
        for end in range(start + 2, len(x) - 2):
            span = float(x[end] - x[start])
            if span < min_span:
                continue
            if span > max_span:
                break
            rise = float(smoothed[start] - smoothed[end])
            if rise < min_rise:
                continue
            after_end = min(len(x), end + 5)
            after_peak = (
                float(np.nanmax(smoothed[end:after_end]))
                if end < after_end
                else float(smoothed[end])
            )
            if after_peak > smoothed[end] + (0.9 * rise):
                continue
            score = float(x[start]) + (span * 0.2) - (rise * 0.08)
            candidates.append((score, start, end, rise))
    if not candidates:
        return None

    _score, start, end, rise = min(candidates, key=lambda item: item[0])
    before_start = max(0, start - 6)
    after_end = min(len(x), end + 7)
    before = _line_segment_fit(x[before_start : start + 1], y[before_start : start + 1])
    transition = _line_segment_fit(x[start : end + 1], y[start : end + 1])
    after = _line_segment_fit(x[end:after_end], y[end:after_end])
    if before is None or transition is None or after is None:
        return None
    if transition.slope >= 0.0:
        return None
    rmse = _combined_segment_rmse(
        (
            (before.rmse, start + 1 - before_start),
            (transition.rmse, end + 1 - start),
            (after.rmse, after_end - end),
        )
    )
    linear_rmse = _linear_rmse(x, y)
    fit = TangentTransitionFit(
        start_x=float(x[start]),
        finish_x=float(x[end]),
        before=before,
        transition=transition,
        after=after,
        rmse=rmse,
    )
    return _AnnealingTransitionCandidate(
        fit=fit,
        linear_rmse=linear_rmse,
        score=float(x[start]) - rise + (fit.finish_x - fit.start_x),
        count=len(x),
    )


def _line_segment_fit(x_values: np.ndarray, y_values: np.ndarray) -> LinearSegmentFit | None:
    mask = np.isfinite(x_values) & np.isfinite(y_values)
    x = x_values[mask]
    y = y_values[mask]
    if len(x) < 2 or len(np.unique(x)) < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    fitted = (slope * x) + intercept
    rmse = float(np.sqrt(np.mean((y - fitted) ** 2)))
    return LinearSegmentFit(
        slope=float(slope),
        intercept=float(intercept),
        start_x=float(np.nanmin(x)),
        end_x=float(np.nanmax(x)),
        rmse=rmse,
    )


def _combined_segment_rmse(segments: Tuple[Tuple[float, int], ...]) -> float:
    total = sum(count for _rmse, count in segments)
    if total <= 0:
        return float("inf")
    return float(math.sqrt(sum((rmse**2) * count for rmse, count in segments) / total))


def _has_resistance_drop_candidate(current_values: np.ndarray, resistance_values: np.ndarray) -> bool:
    mask = np.isfinite(current_values) & np.isfinite(resistance_values)
    x = current_values[mask]
    y = resistance_values[mask]
    if len(x) < 24:
        return False
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    scan_width = float(np.nanmax(x) - np.nanmin(x))
    if not math.isfinite(scan_width) or scan_width <= 0.0:
        return False
    smoothed = (
        pd.Series(y)
        .rolling(window=5, center=True, min_periods=1)
        .median()
        .to_numpy(dtype=float)
    )
    diffs = np.diff(smoothed)
    finite_diffs = np.abs(diffs[np.isfinite(diffs)])
    noise = float(np.nanmedian(finite_diffs)) if finite_diffs.size else 0.0
    median_resistance = float(np.nanmedian(np.abs(smoothed)))
    threshold = max(0.04 * median_resistance, 8.0 * noise)
    min_span = scan_width * 0.05
    for start in range(0, len(x) - 3):
        later = np.where((x[start + 1 :] - x[start]) >= min_span)[0]
        if later.size == 0:
            continue
        end_values = later + start + 1
        if float(smoothed[start] - np.nanmin(smoothed[end_values])) >= threshold:
            return True
    return False


def _linear_rmse(x_values: np.ndarray, y_values: np.ndarray) -> float:
    mask = np.isfinite(x_values) & np.isfinite(y_values)
    x = x_values[mask]
    y = y_values[mask]
    if len(x) < 2 or len(np.unique(x)) < 2:
        return float("inf")
    slope, intercept = np.polyfit(x, y, 1)
    fitted = (slope * x) + intercept
    return float(np.sqrt(np.mean((y - fitted) ** 2)))


def _format_compact_number(value: float, *, max_decimals: int = 2) -> str:
    rounded = round(float(value), max_decimals)
    if math.isclose(rounded, round(rounded), abs_tol=0.5 * (10**-max_decimals)):
        return str(int(round(rounded)))
    return f"{rounded:.{max_decimals}f}".rstrip("0").rstrip(".")


def _normalise_diameter_um(value: object) -> float | None:
    try:
        diameter_um = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(diameter_um) or diameter_um <= 0:
        return None
    return diameter_um


def _wire_area_mm2_from_diameter_um(diameter_um: object) -> float | None:
    normalised = _normalise_diameter_um(diameter_um)
    if normalised is None:
        return None
    diameter_mm = normalised / 1000.0
    return math.pi * (diameter_mm**2) / 4.0


def _current_density_a_per_mm2(
    current_mA: float | int | None,
    diameter_um: object,
) -> float | None:
    area_mm2 = _wire_area_mm2_from_diameter_um(diameter_um)
    if area_mm2 is None or area_mm2 <= 0:
        return None
    try:
        current_value_mA = float(current_mA)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current_value_mA):
        return None
    return (current_value_mA / 1000.0) / area_mm2


def _current_axis_label(currents: np.ndarray, diameter_um: object) -> str:
    base_label = "Current [mA]"
    normalised_diameter = _normalise_diameter_um(diameter_um)
    if normalised_diameter is None:
        return base_label
    finite_currents = np.asarray(currents, dtype=float)
    finite_currents = finite_currents[np.isfinite(finite_currents)]
    if finite_currents.size == 0:
        return base_label
    max_current_mA = float(np.nanmax(np.abs(finite_currents)))
    density = _current_density_a_per_mm2(max_current_mA, normalised_diameter)
    if density is None:
        return base_label
    return (
        f"{base_label} "
        f"({_format_compact_number(max_current_mA, max_decimals=0)} mA = "
        f"{_format_compact_number(density, max_decimals=0)} A/mm², "
        f"d = {_format_compact_number(normalised_diameter)} µm)"
    )


def _origin_current_axis_label(currents: np.ndarray, diameter_um: object) -> str:
    return _current_axis_label(currents, diameter_um).replace("[mA]", "(mA)")


def _add_current_density_top_axis(ax: Any, diameter_um: object) -> Any | None:
    area_mm2 = _wire_area_mm2_from_diameter_um(diameter_um)
    if area_mm2 is None or area_mm2 <= 0:
        return None
    top_ax = ax.twiny()
    top_ax.set_xlim(ax.get_xlim())
    ticks = [
        float(tick)
        for tick in ax.get_xticks()
        if isinstance(tick, (int, float, np.floating)) and math.isfinite(float(tick))
    ]
    top_ax.set_xticks(ticks)
    top_ax.set_xticklabels(
        [
            _format_compact_number((tick / 1000.0) / area_mm2, max_decimals=0)
            for tick in ticks
        ]
    )
    top_ax.set_xlabel("Current density [A/mm²]", fontsize=AXIS_LABEL_SIZE)
    top_ax.tick_params(axis="x", labelsize=TICK_SIZE)
    return top_ax


def _normalise_origin_mode(mode: str | None) -> str:
    # Backwards compatibility: older projects may persist "experimental"/"simple".
    normalised = str(mode or "").strip().lower()
    if normalised in {"directional", "experimental", "split"}:
        return ORIGIN_MODES[0]
    return ORIGIN_MODES[0]


def _segment_label(direction: float, cycle_index: int) -> str:
    prefix = "Increasing" if direction >= 0 else "Decreasing"
    return f"{prefix} {cycle_index}"


def _segment_color(direction: float, cycle_index: int) -> str:
    palette = INCREASING_COLORS if direction >= 0 else DECREASING_COLORS
    return palette[(max(cycle_index, 1) - 1) % len(palette)]


def _clear_layer(layer: Any) -> None:
    remover = getattr(layer, "remove_plot", None)
    if callable(remover):
        removed = False
        try:
            count = len(layer)  # type: ignore[arg-type]
        except Exception:
            count = getattr(layer, "plot_count", None)
        if isinstance(count, int):
            for idx in range(count - 1, -1, -1):
                try:
                    remover(idx)
                    removed = True
                except Exception:
                    pass
        if not removed:
            for _ in range(8):
                try:
                    remover(0)
                    removed = True
                except Exception:
                    break
    clearer = getattr(layer, "clear", None)
    if callable(clearer):
        try:
            clearer()
        except Exception:
            pass


def _legend_label(layer: Any) -> Any | None:
    label_method = getattr(layer, "label", None)
    if not callable(label_method):
        return None
    try:
        legend = label_method("Legend")
    except Exception:
        legend = None
    if legend is None or not hasattr(legend, "text"):
        return None
    return cast(Any, legend)


def _set_graph_title(
    layer: Any,
    text: str,
    *,
    graph: Any | None = None,
    origin_any: Any | None = None,
) -> None:
    if graph is not None:
        try:
            shared_set_origin_graph_title(origin_any, graph, layer, text)
            return
        except Exception:
            pass
    applied = False
    label_method = getattr(layer, "label", None)
    if not callable(label_method):
        title_label = None
    else:
        try:
            title_label = label_method("Title")
        except Exception:
            title_label = None
    if title_label is not None and hasattr(title_label, "text"):
        try:
            cast(Any, title_label).text = text
            _set_visibility(title_label, True)
            applied = True
        except Exception:
            pass
    # Fallback for templates where the Title label object is not exposed.
    cmd = f'title -s "{_escape_ltalk_text(text)}";'
    for target in (graph, layer, origin_any):
        exec_lt = getattr(target, "lt_exec", None) if target is not None else None
        if not callable(exec_lt):
            continue
        try:
            exec_lt(cmd)
            applied = True
            break
        except Exception:
            continue
    _ = applied


def _place_current_annealing_title(layer: Any, text: str) -> None:
    label_method = getattr(layer, "label", None)
    title_label = None
    if callable(label_method):
        try:
            title_label = label_method("Title")
        except Exception:
            title_label = None
    if title_label is None:
        return
    try:
        title_label.text = text
    except Exception:
        pass
    _set_visibility(title_label, True)
    _set_text_size(title_label, max(11, min(TITLE_SIZE, 12)))
    get_float = getattr(layer, "get_float", None)
    set_float = getattr(title_label, "set_float", None)
    if not callable(get_float) or not callable(set_float):
        return
    try:
        x_from = float(get_float("x.from"))
        x_to = float(get_float("x.to"))
        y_from = float(get_float("y.from"))
        y_to = float(get_float("y.to"))
    except Exception:
        return
    if not all(math.isfinite(value) for value in (x_from, x_to, y_from, y_to)):
        return
    y_span = y_to - y_from
    if y_span <= 0:
        return
    for key, value in (
        ("x", (x_from + x_to) / 2.0),
        ("y", y_to + (y_span * 0.34)),
    ):
        try:
            set_float(key, float(value))
        except Exception:
            pass


def _assign_long_name(target: Any | None, name: str) -> None:
    if target is None:
        return
    for attr in ("long_name", "longname", "lname"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, name)
                return
            except Exception:
                continue


def _set_text_size(target: Any | None, size: int) -> bool:
    if target is None:
        return False
    for attr in ("font_size", "fontsize", "text_size", "height", "size", "FontSize"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, size)
                return True
            except Exception:
                continue
    setter = getattr(target, "set_size", None)
    if callable(setter):
        try:
            setter(size)
            return True
        except Exception:
            return False
    return False


def _set_visibility(target: Any | None, visible: bool) -> bool:
    if target is None:
        return False
    for attr in ("visible", "Visible", "show", "Show"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, bool(visible))
                return True
            except Exception:
                continue
    return False


def _apply_axis_labels(layer: Any, x_label: str, y_label: str) -> None:
    axis_method = getattr(layer, "axis", None)
    if not callable(axis_method):
        return
    show_axis = bool(globals().get("SHOW_AXIS_LABELS", True))
    axis_size = int(globals().get("AXIS_LABEL_SIZE", 18))
    for axis_name, label_text in (("x", x_label), ("y", y_label)):
        try:
            axis_obj = axis_method(axis_name)
        except Exception:
            axis_obj = None
        if axis_obj is None:
            continue
        label_obj = getattr(axis_obj, "label", None)
        text_value = label_text if show_axis else ""
        if label_obj is not None and hasattr(label_obj, "text"):
            try:
                cast(Any, label_obj).text = text_value
                _set_visibility(label_obj, show_axis)
                if show_axis:
                    _set_text_size(label_obj, axis_size)
                continue
            except Exception:
                pass
        for attr in ("title", "text"):
            if hasattr(axis_obj, attr):
                try:
                    setattr(cast(Any, axis_obj), attr, text_value)
                    break
                except Exception:
                    continue

        if not show_axis:
            for attr in ("label", "Label"):
                sub = getattr(axis_obj, attr, None)
                if sub is not None:
                    _set_visibility(sub, False)


def _apply_origin_power_top_axis(
    layer: Any,
    workbook: Any | None,
    worksheet: Any | None,
    currents: np.ndarray,
    resistances: np.ndarray,
) -> None:
    finite_current = np.asarray(currents, dtype=float)
    finite_current = finite_current[np.isfinite(finite_current)]
    if finite_current.size < 2:
        return
    current_min = float(np.nanmin(finite_current))
    current_max = float(np.nanmax(finite_current))
    if not math.isfinite(current_min) or not math.isfinite(current_max):
        return
    if math.isclose(current_min, current_max, abs_tol=1e-12):
        return
    finite_mask = np.isfinite(np.asarray(currents, dtype=float)) & np.isfinite(
        np.asarray(resistances, dtype=float)
    )
    if not finite_mask.any():
        return
    power_values = (np.asarray(currents, dtype=float)[finite_mask] ** 2) * np.asarray(
        resistances, dtype=float
    )[finite_mask] / 1000.0
    current_values = np.asarray(currents, dtype=float)[finite_mask]
    grouped = pd.DataFrame({"current": current_values, "power": power_values}).groupby(
        "current", sort=True, as_index=False
    )["power"].median()
    if len(grouped) < 2:
        return
    try:
        x_values = grouped["current"].to_numpy(dtype=float)
        p_values = grouped["power"].to_numpy(dtype=float)
        denominator = float(np.sum(x_values**4))
        if math.isclose(denominator, 0.0, abs_tol=1e-12):
            return
        quad = float(np.sum((x_values**2) * p_values) / denominator)
    except Exception:
        return
    formula = f"({quad:.12g})*x^2"
    lt_exec = getattr(layer, "lt_exec", None)
    if not callable(lt_exec):
        return
    commands = [
        "axis -ps X A 3;",
        "axis -ps X L 3;",
        "layer.x.showAxes=3;",
        "layer.x.showlabel=1;",
        "layer.x.showLabels=3;",
        "layer.x2.showlabel=1;",
        "layer.x2.showLabels=3;",
        "layer.x2.label.type=1;",
        "layer.x2.labelType=1;",
        f'layer.x2.label.formula$="{formula}";',
        "layer.x2.label.decPlaces=1;",
        "layer.x.ticks=10;",
        "layer.x2.ticks=10;",
        "layer.x2.label.fsize=10;",
    ]
    for command in commands:
        try:
            lt_exec(command)
        except Exception:
            continue
    try:
        shared_set_origin_axis_title(layer, "x2", "Power [mW]")
    except Exception:
        try:
            lt_exec('label -s -xt "Power [mW]";')
        except Exception:
            pass


def _apply_tick_settings(layer: Any, axis_name: str, axis_obj: Any | None) -> None:
    show_ticks = bool(globals().get("SHOW_TICK_LABELS", True))
    tick_size = int(globals().get("TICK_SIZE", 18))

    tick_obj: Any | None = None
    for attr in ("tick_labels", "tickLabels", "ticklabel", "TickLabels"):
        candidate = getattr(axis_obj, attr, None) if axis_obj is not None else None
        if candidate is not None:
            tick_obj = candidate
            break

    if tick_obj is not None:
        _set_visibility(tick_obj, show_ticks)
        if show_ticks:
            _set_text_size(tick_obj, tick_size)

    setter = getattr(axis_obj, "show", None)
    if callable(setter):
        try:
            setter(show_ticks)
        except Exception:
            pass


def _prepare_origin_workspace(
    currents: np.ndarray,
    resistances: np.ndarray,
    title: str,
    source_name: str,
) -> Tuple[Any, Any | None, Any | None, Any | None, Any | None, str]:
    import originpro as op  # lazy import

    origin_any: Any = cast(Any, op)
    try:
        origin_any.set_show()
    except Exception:
        pass

    source_stem = Path(source_name).stem or title
    legend_label = source_stem or title
    workbook_name = (source_stem or title)[:32] or "Annealing"

    workbook: Any | None
    try:
        book_obj = origin_any.new_book('w', lname=workbook_name)
        workbook = cast(Any, book_obj) if book_obj is not None else None
    except Exception:
        workbook = None

    worksheet: Any | None = None
    if workbook is not None:
        try:
            worksheet = cast(Any, workbook[0])
        except Exception:
            worksheet = None
    if worksheet is None:
        sheet_obj: Any | None
        try:
            sheet_obj = origin_any.new_sheet('w', lname='Data')
        except Exception:
            sheet_obj = None
        if sheet_obj is not None:
            worksheet = cast(Any, sheet_obj)
            try:
                workbook = getattr(worksheet, 'parent', workbook)
            except Exception:
                pass
    if worksheet is None:
        return origin_any, None, None, None, None, legend_label

    try:
        worksheet.from_list(0, currents.tolist())
        worksheet.from_list(1, resistances.tolist())
    except Exception:
        return origin_any, None, None, None, None, legend_label
    try:
        worksheet.cols_axis('XY')
    except Exception:
        pass
    try:
        worksheet.header_rows("LUC")
    except Exception:
        pass
    try:
        worksheet.set_label(0, "Current", "L")
        worksheet.set_label(0, "mA", "U")
        worksheet.set_label(0, legend_label, "C")
        worksheet.set_label(1, "Resistance", "L")
        worksheet.set_label(1, "Ω", "U")
        worksheet.set_label(1, legend_label, "C")
    except Exception:
        pass

    graph: Any | None
    try:
        graph_obj = origin_any.new_graph(template='scatter')
        graph = cast(Any, graph_obj) if graph_obj is not None else None
    except Exception:
        graph = None
    if graph is None:
        return origin_any, workbook, worksheet, None, None, legend_label

    try:
        graph.activate()
    except Exception:
        pass

    try:
        layer = cast(Any, graph[0])
    except Exception:
        layer = None

    if layer is not None:
        _clear_layer(layer)

    return origin_any, workbook, worksheet, graph, layer, legend_label


def _apply_origin_readability(layer: Any, graph: Any | None) -> None:
    if layer is None:
        return
    legend = _legend_label(layer)
    show_legend = bool(globals().get("SHOW_LEGEND", True))
    legend_size = int(globals().get("LEGEND_SIZE", 18))
    if legend is not None:
        if show_legend:
            _set_visibility(legend, True)
            _set_text_size(legend, legend_size)
        else:
            try:
                legend.text = ""
            except Exception:
                pass
            _set_visibility(legend, False)

    label_method = getattr(layer, "label", None)
    title_label: Any | None = None
    if callable(label_method):
        try:
            title_label = label_method("Title")
        except Exception:
            title_label = None
    show_title = bool(globals().get("SHOW_TITLE", True))
    title_size = int(globals().get("TITLE_SIZE", 22))
    if title_label is not None:
        _set_visibility(title_label, show_title)
        if show_title:
            _set_text_size(title_label, title_size)

    for axis_name in ("x", "y"):
        axis_obj = None
        axis_method = getattr(layer, "axis", None)
        if callable(axis_method):
            try:
                axis_obj = axis_method(axis_name)
            except Exception:
                axis_obj = None
        _apply_tick_settings(layer, axis_name, axis_obj)

    if graph is not None and show_title:
        try:
            graph.activate()
        except Exception:
            pass


def _style_origin_report_layout(layer: Any, *, outside_legend: bool = False) -> None:
    """Reserve page space for native Origin labels and place the legend predictably."""
    if layer is None:
        return
    lt_exec = getattr(layer, "lt_exec", None)
    if not callable(lt_exec):
        return
    layer_box = (
        "layer -u 1; layer 50 46 26 30; "
        "layer.top=30; layer.left=26; layer.width=50; layer.height=46;"
        if outside_legend
        else "layer -u 1; layer 58 46 23 30; "
        "layer.top=30; layer.left=23; layer.width=58; layer.height=46;"
    )
    commands = [
        layer_box,
        "layer.x.ticks=10;",
        "layer.x2.ticks=10;",
        "layer.x.label.fsize=10;",
        "layer.x2.label.fsize=10;",
        "layer.y.label.fsize=10;",
        "layer.x.title.fsize=12;",
        "layer.x2.title.fsize=12;",
        "layer.y.title.fsize=12;",
        "legend.fsize=10;",
    ]
    if outside_legend:
        commands.extend(
            [
                "legend.x=layer.x.to + legend.dx / 2 + abs(layer.x.to - layer.x.from) * 0.04;",
                "legend.y=layer.y.to - legend.dy / 2;",
            ]
        )
    else:
        commands.extend(
            [
                "legend.x=layer.x.from + legend.dx / 2;",
                "legend.y=layer.y.to - legend.dy / 2;",
            ]
        )
    for command in commands:
        try:
            lt_exec(command)
        except Exception:
            continue


def _apply_origin_curve_color(plot_any: Any, color: str) -> None:
    set_cmd = getattr(plot_any, "set_cmd", None)
    if callable(set_cmd):
        try:
            red = int(color[1:3], 16)
            green = int(color[3:5], 16)
            blue = int(color[5:7], 16)
            origin_color = f"color({red},{green},{blue})"
            origin_html_color = f'color("{color}")'
            for command in (
                f"-c {origin_color}",
                f"-cse {origin_html_color}",
                f"-csf {origin_html_color}",
                f"-cr {origin_color}",
                f"-cser {origin_color}",
                f"-csfr {origin_color}",
                f"-cf {origin_color}",
                "-kf 0",
            ):
                try:
                    set_cmd(command)
                except TypeError:
                    set_cmd(command, "")
        except Exception:
            pass
    for attr, value in (
        ("color", color),
        ("line_color", color),
        ("symbol_color", color),
        ("symbol_edge_color", color),
        ("symbol_fill_color", color),
        ("symbol_interior", 1),
    ):
        try:
            setattr(plot_any, attr, value)
        except Exception:
            pass
    symbol = getattr(plot_any, "symbol", None)
    if symbol is not None:
        for attr, value in (
            ("color", color),
            ("edge_color", color),
            ("fill_color", color),
            ("symbol_color", color),
        ):
            try:
                setattr(symbol, attr, value)
            except Exception:
                pass


def _plot_origin_simple(
    workbook: Any | None,
    worksheet: Any | None,
    graph: Any | None,
    layer: Any | None,
    legend_label: str,
    line_label: str,
) -> None:
    if worksheet is None or graph is None or layer is None:
        return
    plot_obj = layer.add_plot(worksheet, coly=1, colx=0, type='y')
    if plot_obj is None:
        return
    plot_any = cast(Any, plot_obj)
    color = '#000000'
    try:
        _apply_origin_curve_color(plot_any, color)
        plot_any.line_width = 1.5
        plot_any.symbol_shape = 2
        plot_any.symbol_size = 4
        plot_any.legend = line_label
    except Exception:
        try:
            plot_any.legend = line_label
        except Exception:
            pass
    dataset_index = getattr(plot_any, 'index', None)
    if not isinstance(dataset_index, int):
        dataset_index = 1
    legend = _legend_label(layer)
    if legend is not None:
        try:
            legend.text = f"\\l({dataset_index}) {line_label}"
        except Exception:
            pass
    try:
        layer.rescale()
    except Exception:
        pass
    try:
        graph.activate()
    except Exception:
        pass


def _plot_origin_experimental(
    origin_any: Any,
    workbook: Any | None,
    worksheet: Any | None,
    graph: Any | None,
    layer: Any | None,
    currents: np.ndarray,
    resistances: np.ndarray,
    legend_label: str,
) -> None:
    if graph is None or layer is None:
        return
    if worksheet is None:
        _plot_origin_simple(
            workbook,
            worksheet,
            graph,
            layer,
            legend_label,
            "Current trace",
        )
        return
    _, segments = _direction_profile(currents)
    if not segments:
        _plot_origin_simple(
            workbook,
            worksheet,
            graph,
            layer,
            legend_label,
            "Current trace",
        )
        return

    previous_direction: float | None = None

    legend_entries: List[Tuple[int, str]] = []

    def _set_column_meta(col_index: int, label: str, is_x: bool) -> None:
        try:
            col = worksheet.obj.Columns(col_index)
        except Exception:
            col = None
        if col is not None:
            try:
                col.LongName = "Current" if is_x else "Resistance"
                col.Units = "mA" if is_x else "Ω"
                col.Comment = label
                col.Type = 3 if is_x else 4
            except Exception:
                pass
        else:
            try:
                worksheet.set_label(col_index, "Current" if is_x else "Resistance", "L")
                worksheet.set_label(col_index, "mA" if is_x else "Ω", "U")
                worksheet.set_label(col_index, label, "C")
            except Exception:
                pass

    def _write_series(col_index: int, data_x: List[float], data_y: List[float], label: str) -> bool:
        if not data_x:
            return False
        try:
            worksheet.from_list(col_index, data_x)
            worksheet.from_list(col_index + 1, data_y)
        except Exception:
            return False
        _set_column_meta(col_index, label, True)
        _set_column_meta(col_index + 1, label, False)
        return True

    def _add_direction_plot(col_index: int, label: str, color: str) -> None:
        plot_obj = layer.add_plot(worksheet, coly=col_index + 1, colx=col_index, type='y')
        if plot_obj is None:
            return
        plot_any = cast(Any, plot_obj)
        try:
            plot_any.color = color
            plot_any.line_width = 1.5
            plot_any.symbol_shape = 2
            plot_any.symbol_size = 4
            plot_any.symbol_edge_color = color
            plot_any.symbol_fill_color = color
            plot_any.legend = ''
        except Exception:
            try:
                plot_any.legend = ''
            except Exception:
                pass
        dataset_index = getattr(plot_any, 'index', None)
        if not isinstance(dataset_index, int):
            try:
                dataset_index = getattr(layer, 'plot_count', None)
            except Exception:
                dataset_index = None
        if not isinstance(dataset_index, int):
            try:
                dataset_index = len(layer)  # type: ignore[arg-type]
            except Exception:
                dataset_index = None
        if not isinstance(dataset_index, int):
            dataset_index = len(legend_entries) + 1
        legend_entries.append((dataset_index, label))
        _apply_origin_curve_color(plot_any, color)

    try:
        worksheet.header_rows("LUC")
    except Exception:
        pass

    col_index = 0
    inc_count = 0
    dec_count = 0
    for start, end, direction in segments:
        if end <= start:
            previous_direction = direction
            continue
        segment_x = currents[start:end].tolist()
        segment_y = resistances[start:end].tolist()
        if direction < 0 and previous_direction is not None and previous_direction >= 0 and start > 0:
            segment_x.insert(0, float(currents[start - 1]))
            segment_y.insert(0, float(resistances[start - 1]))
        if direction >= 0:
            inc_count += 1
            cycle_index = inc_count
        else:
            dec_count += 1
            cycle_index = dec_count
        label = _segment_label(direction, cycle_index)
        color = _segment_color(direction, cycle_index)
        if _write_series(col_index, segment_x, segment_y, label):
            _add_direction_plot(col_index, label, color)
            col_index += 2
        previous_direction = direction

    if not legend_entries:
        _plot_origin_simple(
            workbook,
            worksheet,
            graph,
            layer,
            legend_label,
            "Current trace",
        )
        return

    legend = _legend_label(layer)
    if legend is not None and legend_entries:
        lines = []
        lines.extend(f"\\l({idx}) {text}" for idx, text in legend_entries if text)
        try:
            legend.text = "\n".join(lines)
        except Exception:
            pass

    try:
        layer.rescale()
    except Exception:
        pass
    try:
        graph.activate()
    except Exception:
        pass
def plot_one(
    df: pd.DataFrame,
    title: str,
    *,
    figsize: Tuple[float, float] | None = None,
    target_px: Tuple[int, int] | None = None,
    show_power_top_axis: bool = False,
    show_current_density_top_axis: bool = True,
    wire_diameter_um: float | None = None,
) -> Tuple[Figure, str]:
    if target_px is not None:
        target_width, target_height = target_px
        dpi = 140.0
        width = max(float(target_width) / dpi, 0.5)
        height = max(float(target_height) / dpi, 0.5)
    else:
        if not figsize:
            figsize = (6.0, 4.0)
        width = max(float(figsize[0]), 0.5)
        height = max(float(figsize[1]), 0.5)
        dpi = 120.0
    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    _, segments = _direction_profile(currents)
    marker_size = 3.0
    line_width = 1.2
    legend_handles: list[Line2D] = []
    if currents.size == 0:
        pass
    elif currents.size == 1:
        line = ax.plot(
            currents,
            resistances,
            marker="o",
            linestyle="None",
            color="r",
            markersize=marker_size,
        )[0]
        line.set_label("Sample")
    else:
        previous_direction: float | None = None
        inc_count = 0
        dec_count = 0
        for start, end, direction in segments:
            if end <= start:
                previous_direction = direction
                continue
            segment_currents = currents[start:end]
            segment_resistances = resistances[start:end]
            if (
                direction < 0
                and previous_direction is not None
                and previous_direction >= 0
                and start > 0
            ):
                segment_currents = np.concatenate(
                    ([currents[start - 1]], segment_currents)
                )
                segment_resistances = np.concatenate(
                    ([resistances[start - 1]], segment_resistances)
                )
            if direction >= 0:
                inc_count += 1
                cycle_index = inc_count
            else:
                dec_count += 1
                cycle_index = dec_count
            label = _segment_label(direction, cycle_index)
            color = _segment_color(direction, cycle_index)
            line = ax.plot(
                segment_currents,
                segment_resistances,
                color=color,
                marker="o",
                linestyle="-",
                markersize=marker_size,
                markerfacecolor=color,
                markeredgecolor=color,
                linewidth=line_width,
                label=label,
            )[0]
            legend_handles.append(
                Line2D(
                    [],
                    [],
                    color=color,
                    marker="o",
                    linestyle="-",
                    markersize=marker_size,
                    linewidth=line_width,
                    label=label,
                )
            )
            previous_direction = direction

    ax.set_xlabel(_current_axis_label(currents, wire_diameter_um), fontsize=AXIS_LABEL_SIZE)
    ax.set_ylabel("Resistance [Ω]", fontsize=AXIS_LABEL_SIZE)
    ax.set_title(title, fontsize=TITLE_SIZE, pad=10)
    ax.grid(True, ls="--", alpha=0.3)
    density_top_axis = None
    if show_power_top_axis:
        add_power_top_axis(
            ax,
            currents,
            resistances,
            label="Power [mW]",
            label_size=AXIS_LABEL_SIZE,
            tick_size=TICK_SIZE,
        )
    elif show_current_density_top_axis:
        density_top_axis = _add_current_density_top_axis(ax, wire_diameter_um)
    if legend_handles:
        legend = ax.legend(
            handles=legend_handles,
            loc="best",
            labelcolor="linecolor",
            handlelength=2.0,
            fontsize=LEGEND_SIZE,
            framealpha=0.9,
        )
        if legend is not None and not LEGEND_SHOW_SYMBOLS:
            handles_attr = getattr(legend, "legendHandles", None)
            if handles_attr is None:
                handles_attr = getattr(legend, "legend_handles", [])
            for handle in handles_attr:
                try:
                    handle.set_marker("")
                except Exception:
                    pass
        if legend is not None:
            try:
                handles_attr = getattr(legend, "legendHandles", None)
                if handles_attr is None:
                    handles_attr = getattr(legend, "legend_handles", [])
                for handle, text in zip(handles_attr, legend.get_texts()):
                    color = None
                    get_color = getattr(handle, "get_color", None)
                    if callable(get_color):
                        color = get_color()
                    if color is None:
                        continue
                    text.set_color(color)
            except Exception:
                pass
    ax.tick_params(axis="both", labelsize=TICK_SIZE)
    fig.tight_layout()
    cfg = dict(globals())
    apply_readability(ax, cfg)
    if density_top_axis is not None:
        try:
            density_top_axis.set_xlim(ax.get_xlim())
        except Exception:
            pass
    legend = ax.get_legend()
    if legend is not None:
        try:
            handles_attr = getattr(legend, "legendHandles", None)
            if handles_attr is None:
                handles_attr = getattr(legend, "legend_handles", [])
            for handle, text in zip(handles_attr, legend.get_texts()):
                get_color = getattr(handle, "get_color", None)
                if not callable(get_color):
                    continue
                text.set_color(get_color())
        except Exception:
            pass
    fname = title.replace(os.sep, "_")
    return fig, fname


def plot_one_origin(
    df: pd.DataFrame,
    title: str,
    source_name: str,
    mode: str | None = None,
    *,
    show_power_top_axis: bool = False,
    show_current_density_top_axis: bool = True,
    wire_diameter_um: float | None = None,
    return_handles: bool = False,
) -> dict[str, object] | None:
    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    origin_any, workbook, worksheet, graph, layer, legend_label = _prepare_origin_workspace(
        currents,
        resistances,
        title,
        source_name,
    )
    handles: dict[str, object] = {
        "origin": origin_any,
        "workbook": workbook,
        "worksheet": worksheet,
        "graph": graph,
        "layer": layer,
        "legend_label": legend_label,
    }
    if graph is None or layer is None:
        return handles if return_handles else None

    display_label = _format_origin_annotation(legend_label)
    _ = show_current_density_top_axis
    _apply_axis_labels(
        layer,
        _origin_current_axis_label(currents, wire_diameter_um),
        "Resistance (\u03a9)",
    )
    _set_graph_title(layer, display_label, graph=graph, origin_any=origin_any)
    _assign_long_name(graph, legend_label)
    _assign_long_name(workbook, legend_label)

    _ = _normalise_origin_mode(mode if mode is not None else ORIGIN_MODE)
    _plot_origin_experimental(
        origin_any,
        workbook,
        worksheet,
        graph,
        layer,
        currents,
        resistances,
        legend_label,
    )
    hide_origin_workbook(origin_any, workbook, graph)

    _apply_origin_readability(layer, graph)
    if show_power_top_axis:
        _apply_origin_power_top_axis(layer, workbook, worksheet, currents, resistances)
    _style_origin_report_layout(layer)
    _set_graph_title(layer, display_label, graph=graph, origin_any=origin_any)
    _place_current_annealing_title(layer, display_label)

    if return_handles:
        handles["graph"] = graph
        handles["layer"] = layer
        handles["workbook"] = workbook
        handles["worksheet"] = worksheet
        handles["legend_label"] = legend_label
        return handles

    return None


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    use_matplotlib = wants_matplotlib(backend)
    use_origin = wants_origin(backend)
    keep_open = bool(use_matplotlib and SHOW_PLOTS)
    prev_interactive = plt.isinteractive()
    if use_matplotlib and not SHOW_PLOTS:
        plt.ioff()

    open_figures: List[Figure] = []
    failures: List[Tuple[str, str]] = []
    successes = 0
    output_dir: Path | None = None

    try:
        for path in files:
            try:
                source = resolve_measurement_path(path)
                df = load_file(source)
            except Exception as exc:
                failures.append((path, f"load: {exc}"))
                print(f"ERROR: Failed to read {Path(path).name}: {exc}")
                continue

            title = format_annealing_title(measurement_display_name(source))
            success = True
            fig: Figure | None = None
            fname: str = ""

            if use_matplotlib:
                try:
                    fig, fname = plot_one(df, title)
                    if SAVE_PLOTS:
                        if output_dir is None:
                            output_dir = Path(OUTPUT_DIR)
                            output_dir.mkdir(parents=True, exist_ok=True)
                        save_figure(fig, output_dir / fname, SAVE_FORMAT, PNG_DPI)
                    if keep_open:
                        open_figures.append(fig)
                    else:
                        plt.close(fig)
                except Exception as exc:
                    failures.append((path, f"matplotlib: {exc}"))
                    print(
                        f"ERROR: Matplotlib plot failed for {Path(path).name}: {exc}"
                    )
                    success = False
                    if fig is not None:
                        plt.close(fig)

            if use_origin:
                try:
                    plot_one_origin(df, title, Path(path).name)
                except Exception as e:
                    print(f"Origin plot failed for {title}: {e}")

            if success:
                successes += 1
    finally:
        if use_origin:
            schedule_origin_release()

    if use_matplotlib:
        if keep_open and open_figures:
            show_plots()
        elif not keep_open:
            plt.close("all")

    if use_matplotlib and not SHOW_PLOTS and prev_interactive:
        plt.ion()

    total = successes + len(failures)
    if total:
        print(f"Summary: processed {successes} of {total} file(s).")
        if failures:
            for path, reason in failures:
                print(f"  Skipped {Path(path).name}: {reason}")
    else:
        print("No files supplied for plotting.")
