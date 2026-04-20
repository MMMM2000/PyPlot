from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import List, Tuple

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from plotting.shared.readability import apply_readability

SHOW_LEGEND = True
LEGEND_SIZE = 12
TICK_SIZE = 10
AXIS_LABEL_SIZE = 12
TITLE_SIZE = 16
MAX_ABS_TEMPERATURE_C = 250.0
RESISTANCE_DROPOUT_RATIO = 0.1
RESISTANCE_DROPOUT_WINDOW = 9

_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


@dataclass(frozen=True)
class RVsTSegment:
    label: str
    kind: str
    x: np.ndarray
    y: np.ndarray
    frame: pd.DataFrame


def format_rvst_title(base: str) -> str:
    tokens = [token for token in re.split(r"[_\s]+", str(base).strip()) if token]
    if not tokens:
        return str(base)
    composition = tokens[0].translate(_SUBSCRIPT_MAP)
    if len(tokens) == 1:
        return composition
    if not tokens[1].isdigit():
        return " ".join([composition, *tokens[1:]])

    draw = tokens[1]
    piece_digits = ""
    piece_suffix = ""
    trailing: list[str] = []
    if len(tokens) >= 3:
        match = re.match(r"^(?P<piece>\d+)(?P<suffix>.*)$", tokens[2])
        if match:
            piece_digits = match.group("piece") or ""
            piece_suffix = (match.group("suffix") or "").strip()
        else:
            piece_suffix = tokens[2]
        trailing = tokens[3:]

    microwire = draw
    if piece_digits:
        microwire = f"{draw}/{piece_digits}"
    suffix_tokens = [token for token in [piece_suffix, *trailing] if token]
    if suffix_tokens:
        return f"{composition} {microwire} {' '.join(suffix_tokens)}"
    return f"{composition} {microwire}"


def load_file(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", dtype=str)
    required = ["iso_time", "t_elapsed_s", "sp_c", "pv_c", "resistance_ohm"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")

    frame = df.loc[:, required].copy()
    for column in ("t_elapsed_s", "sp_c", "pv_c", "resistance_ohm"):
        frame[column] = pd.to_numeric(
            frame[column].astype(str).str.replace(",", ".", regex=False).str.strip(),
            errors="coerce",
        )
    frame = frame.dropna(subset=["pv_c", "resistance_ohm"]).reset_index(drop=True)
    frame = frame.loc[frame["pv_c"].abs() <= MAX_ABS_TEMPERATURE_C].reset_index(drop=True)
    if not frame.empty:
        resistance_abs = frame["resistance_ohm"].abs()
        local_median = resistance_abs.rolling(
            window=RESISTANCE_DROPOUT_WINDOW,
            center=True,
            min_periods=3,
        ).median()
        local_median = local_median.fillna(resistance_abs.median())
        valid_resistance = (local_median <= 0) | (
            resistance_abs >= local_median * RESISTANCE_DROPOUT_RATIO
        )
        frame = frame.loc[valid_resistance].reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"{path}: no usable samples after parsing")
    return frame


def _direction_profile(values: np.ndarray) -> Tuple[np.ndarray, List[Tuple[int, int, float]]]:
    count = values.size
    if count == 0:
        return np.array([], dtype=float), []
    if count == 1:
        return np.array([1.0], dtype=float), [(0, 1, 1.0)]

    deltas = np.diff(values)
    finite = np.abs(deltas[np.isfinite(deltas)])
    if finite.size:
        tolerance = max(float(np.quantile(finite, 0.25) * 0.25), 0.02)
    else:
        tolerance = 0.02

    signed = np.sign(deltas)
    signed[np.abs(deltas) <= tolerance] = 0.0
    direction = pd.Series(signed, index=range(1, count))
    direction = direction.replace(0.0, np.nan).reindex(range(count))
    if direction.isna().all():
        direction[:] = 1.0
    else:
        direction = direction.ffill().bfill()
    smoothed = (
        direction.rolling(window=min(5, count), center=True, min_periods=1)
        .median()
        .ffill()
        .bfill()
    )
    smoothed_values = np.where(smoothed.to_numpy(dtype=float) >= 0, 1.0, -1.0)

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


def split_heating_cooling(df: pd.DataFrame) -> list[RVsTSegment]:
    if df.empty:
        return []
    temperatures = pd.to_numeric(df["pv_c"], errors="coerce").to_numpy(dtype=float)
    setpoints = (
        pd.to_numeric(df["sp_c"], errors="coerce").to_numpy(dtype=float)
        if "sp_c" in df.columns
        else temperatures.copy()
    )
    resistances = pd.to_numeric(df["resistance_ohm"], errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(temperatures) & np.isfinite(setpoints) & np.isfinite(resistances)
    frame = df.loc[mask].reset_index(drop=True)
    temperatures = temperatures[mask]
    setpoints = setpoints[mask]
    resistances = resistances[mask]
    if temperatures.size == 0:
        return []

    _, raw_segments = _direction_profile(setpoints)
    heating_count = 0
    cooling_count = 0
    segments: list[RVsTSegment] = []
    for index, (start, end, direction) in enumerate(raw_segments):
        if end <= start:
            continue
        slice_start = start if index == 0 else max(0, start - 1)
        segment_frame = frame.iloc[slice_start:end].reset_index(drop=True)
        segment_x = temperatures[slice_start:end]
        segment_y = resistances[slice_start:end]
        if direction >= 0:
            heating_count += 1
            label = f"Heating {heating_count}"
            kind = "heating"
        else:
            cooling_count += 1
            label = f"Cooling {cooling_count}"
            kind = "cooling"
        segments.append(
            RVsTSegment(
                label=label,
                kind=kind,
                x=segment_x,
                y=segment_y,
                frame=segment_frame,
            )
        )
    return segments


def plot_one(
    df: pd.DataFrame,
    title: str,
    *,
    figsize: tuple[float, float] = (6.0, 4.0),
) -> tuple[Figure, str]:
    fig, ax = plt.subplots(figsize=figsize)
    segments = split_heating_cooling(df)
    legend_handles: list[Line2D] = []
    legend_kinds: set[str] = set()
    palette = {"heating": "#d32f2f", "cooling": "#1976d2"}
    names = {"heating": "Heating", "cooling": "Cooling"}

    for segment in segments:
        color = palette[segment.kind]
        ax.plot(
            segment.x,
            segment.y,
            color=color,
            marker="o",
            markersize=3.2,
            linewidth=1.8,
            label=names[segment.kind] if segment.kind not in legend_kinds else "_nolegend_",
        )
        if segment.kind not in legend_kinds:
            legend_handles.append(
                Line2D(
                    [],
                    [],
                    color=color,
                    marker="o",
                    linestyle="-",
                    markersize=4.0,
                    linewidth=1.8,
                    label=names[segment.kind],
                )
            )
            legend_kinds.add(segment.kind)

    ax.set_xlabel("Temperature [°C]", fontsize=AXIS_LABEL_SIZE)
    ax.set_ylabel("Resistance [Ω]", fontsize=AXIS_LABEL_SIZE)
    ax.set_title(title, fontsize=TITLE_SIZE, pad=10)
    ax.grid(True, ls="--", alpha=0.3)
    if SHOW_LEGEND and legend_handles:
        legend = ax.legend(
            handles=legend_handles,
            loc="best",
            labelcolor="linecolor",
            handlelength=2.0,
            fontsize=LEGEND_SIZE,
            framealpha=0.9,
        )
        if legend is not None:
            handles_attr = getattr(legend, "legendHandles", None)
            if handles_attr is None:
                handles_attr = getattr(legend, "legend_handles", [])
            for handle in handles_attr:
                try:
                    handle.set_marker("")
                except Exception:
                    pass
            for handle, text in zip(handles_attr, legend.get_texts()):
                get_color = getattr(handle, "get_color", None)
                if callable(get_color):
                    text.set_color(get_color())
    ax.tick_params(axis="both", labelsize=TICK_SIZE)
    fig.tight_layout()
    apply_readability(ax, dict(globals()))
    return fig, title.replace(os.sep, "_")
