from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd
from matplotlib.figure import Figure

STRAIN_DIRECTION_TOLERANCE = 1e-9
LOADING_COLORS = ("#1f77b4", "#2ca02c", "#17becf", "#9467bd")
UNLOADING_COLORS = ("#d62728", "#ff7f0e", "#8c564b", "#e377c2")
HOLD_COLORS = ("#7f7f7f",)


@dataclass(frozen=True)
class DirectionSegment:
    direction: int
    start_index: int
    end_index: int
    label: str
    color: str


def _parse_float(token: str) -> float:
    return float(token.strip().replace(",", "."))


def load_manual_stress_strain_file(path: Path) -> pd.DataFrame:
    """Parse manual stress/strain logger TXT output into numeric columns."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read file {path}") from exc

    if len(lines) < 3:
        raise ValueError("Expected header rows + at least one data row.")

    rows: list[tuple[float, float, float, float]] = []
    for raw in lines[2:]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [part.strip() for part in stripped.split("\t") if part.strip()]
        if len(parts) < 4:
            parts = [part.strip() for part in stripped.split() if part.strip()]
        if len(parts) < 4:
            continue
        try:
            displacement, load, strain, stress = (_parse_float(part) for part in parts[:4])
        except ValueError:
            continue
        rows.append((displacement, load, strain, stress))

    if not rows:
        raise ValueError("No valid numeric data rows found.")

    return pd.DataFrame(
        rows,
        columns=["displacement_mm", "load_g", "strain_pct", "stress_mpa"],
    )


def split_segments_by_strain_direction(
    strains: Sequence[float],
    *,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> list[tuple[int, int, int]]:
    count = len(strains)
    if count == 0:
        return []
    if count == 1:
        return [(0, 0, 0)]

    segments: list[tuple[int, int, int]] = []
    start_index = 0
    current_direction = 0

    for index in range(1, count):
        delta = float(strains[index]) - float(strains[index - 1])
        direction = 0
        if delta > tolerance:
            direction = 1
        elif delta < -tolerance:
            direction = -1

        if direction == 0:
            continue

        if current_direction == 0:
            current_direction = direction
            continue

        if direction != current_direction:
            segments.append((current_direction, start_index, max(start_index, index - 1)))
            start_index = max(0, index - 1)
            current_direction = direction

    segments.append((current_direction, start_index, count - 1))
    return segments


def build_segment_styles(
    strains: Sequence[float],
    *,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> list[DirectionSegment]:
    segments = split_segments_by_strain_direction(strains, tolerance=tolerance)
    styled: list[DirectionSegment] = []
    loading_index = 0
    unloading_index = 0
    hold_index = 0

    for direction, start_index, end_index in segments:
        if direction > 0:
            loading_index += 1
            label = f"Loading {loading_index}"
            color = LOADING_COLORS[(loading_index - 1) % len(LOADING_COLORS)]
        elif direction < 0:
            unloading_index += 1
            label = f"Unloading {unloading_index}"
            color = UNLOADING_COLORS[(unloading_index - 1) % len(UNLOADING_COLORS)]
        else:
            hold_index += 1
            label = f"Hold {hold_index}"
            color = HOLD_COLORS[(hold_index - 1) % len(HOLD_COLORS)]
        styled.append(
            DirectionSegment(
                direction=direction,
                start_index=start_index,
                end_index=end_index,
                label=label,
                color=color,
            )
        )
    return styled


def make_shape_memory_figure(
    frame: pd.DataFrame,
    *,
    title: str,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> Figure:
    fig = Figure(figsize=(10, 5), constrained_layout=True)
    ax_raw = fig.add_subplot(121)
    ax_stress = fig.add_subplot(122)

    x_raw = frame["displacement_mm"].tolist()
    y_raw = frame["load_g"].tolist()
    x_stress = frame["strain_pct"].tolist()
    y_stress = frame["stress_mpa"].tolist()
    segments = build_segment_styles(x_stress, tolerance=tolerance)

    ax_raw.set_title(f"{title}\nLoad vs Displacement")
    ax_raw.set_xlabel("Displacement (mm)")
    ax_raw.set_ylabel("Load (g)")
    ax_stress.set_title(f"{title}\nStress vs Strain")
    ax_stress.set_xlabel("Strain (%)")
    ax_stress.set_ylabel("Stress (MPa)")

    plotted_raw = _plot_segmented_curve(
        ax_raw,
        x_raw,
        y_raw,
        segments,
    )
    plotted_stress = _plot_segmented_curve(
        ax_stress,
        x_stress,
        y_stress,
        segments,
    )
    if plotted_raw:
        ax_raw.legend(loc="best", fontsize=8)
    if plotted_stress:
        ax_stress.legend(loc="best", fontsize=8)

    ax_raw.grid(True, alpha=0.3)
    ax_stress.grid(True, alpha=0.3)
    return fig


def make_load_displacement_figure(
    frame: pd.DataFrame,
    *,
    title: str,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> Figure:
    fig = Figure(figsize=(7.5, 5), constrained_layout=True)
    ax = fig.add_subplot(111)
    x_raw = frame["displacement_mm"].tolist()
    y_raw = frame["load_g"].tolist()
    segments = build_segment_styles(frame["strain_pct"].tolist(), tolerance=tolerance)

    ax.set_title(f"{title}\nLoad vs Displacement")
    ax.set_xlabel("Displacement (mm)")
    ax.set_ylabel("Load (g)")

    if _plot_segmented_curve(ax, x_raw, y_raw, segments):
        ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    return fig


def make_stress_strain_figure(
    frame: pd.DataFrame,
    *,
    title: str,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> Figure:
    fig = Figure(figsize=(7.5, 5), constrained_layout=True)
    ax = fig.add_subplot(111)
    x_stress = frame["strain_pct"].tolist()
    y_stress = frame["stress_mpa"].tolist()
    segments = build_segment_styles(x_stress, tolerance=tolerance)

    ax.set_title(f"{title}\nStress vs Strain")
    ax.set_xlabel("Strain (%)")
    ax.set_ylabel("Stress (MPa)")

    if _plot_segmented_curve(ax, x_stress, y_stress, segments):
        ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    return fig


def make_dual_axis_overlay_figure(
    frame: pd.DataFrame,
    *,
    title: str,
    tolerance: float = STRAIN_DIRECTION_TOLERANCE,
) -> Figure:
    fig = Figure(figsize=(8.5, 5), constrained_layout=True)
    ax_load = fig.add_subplot(111)
    ax_stress_y = ax_load.twinx()
    ax_stress = ax_stress_y.twiny()

    x_raw = frame["displacement_mm"].tolist()
    y_raw = frame["load_g"].tolist()
    x_stress = frame["strain_pct"].tolist()
    y_stress = frame["stress_mpa"].tolist()
    segments = build_segment_styles(x_stress, tolerance=tolerance)

    ax_load.set_title(title)
    ax_load.set_xlabel("Displacement (mm)")
    ax_load.set_ylabel("Load (g)")
    ax_stress.set_xlabel("Strain (%)")
    ax_stress.set_ylabel("Stress (MPa)")
    ax_stress_y.set_ylabel("Stress (MPa)")

    ax_stress.patch.set_alpha(0.0)
    ax_stress_y.patch.set_alpha(0.0)
    ax_stress_y.xaxis.set_visible(False)
    ax_stress.yaxis.set_visible(False)

    load_plotted = _plot_segmented_curve(
        ax_load,
        x_raw,
        y_raw,
        segments,
    )
    _plot_segmented_curve(
        ax_stress,
        x_stress,
        y_stress,
        segments,
        linestyle="--",
        linewidth=1.4,
        markersize=3.5,
    )

    if load_plotted:
        ax_load.legend(loc="upper left", fontsize=8)

    ax_load.grid(True, alpha=0.3)
    return fig


def _plot_segmented_curve(
    axis: object,
    x_values: Sequence[float],
    y_values: Sequence[float],
    segments: Sequence[DirectionSegment],
    *,
    label_prefix: str = "",
    linestyle: str = "-",
    linewidth: float = 1.6,
    markersize: float = 4.0,
) -> bool:
    plotted = False
    for segment in segments:
        start_index = segment.start_index
        end_index = min(
            segment.end_index,
            len(x_values) - 1,
            len(y_values) - 1,
        )
        if end_index < start_index:
            continue
        axis.plot(
            x_values[start_index : end_index + 1],
            y_values[start_index : end_index + 1],
            color=segment.color,
            marker="o",
            linestyle=linestyle,
            linewidth=linewidth,
            markersize=markersize,
            label=f"{label_prefix}{segment.label}",
        )
        plotted = True
    return plotted
