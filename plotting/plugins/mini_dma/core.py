from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Collection, Iterable, Sequence

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from plotting.shared.power_axis import add_power_top_axis
from plotting.shared.transition_analysis import (
    LinearSegmentFit,
    TangentTransitionFit,
    fit_tangent_transition,
)

MEASUREMENT_FILE = "measurement.csv"
MINI_DMA_EXCLUDED_DISCOVERY_DIR_NAMES = frozenset(
    {
        ".cache",
        ".pytest_cache",
        "__pycache__",
        "archive",
        "archives",
        "automated",
        "automated_control_tests",
        "automation_history",
        "cache",
        "scratch",
        "temp",
        "test",
        "tests",
        "tmp",
    }
)
PLOT_PHASES = {"current"}
SUMMARY_PHASES = {"current", "current_hold"}
ISO_CURRENT_RECIPE_MODES = {"constant_current_strain_sweep", "iso-current", "iso_current"}
ISO_CURRENT_PHASES = {"current_zero", "target_ramp", "current", "current_hold"}
TRANSITION_REVIEW_RECIPE_MODES = {"current_sweep_stress", "iso-stress", "iso_stress"}
TRANSITION_REVIEW_UNSUPPORTED_RECIPE_MODES = {
    "constant_current_strain_sweep",
    "current_sweep_strain",
    "iso-current",
    "iso_current",
    "iso-strain",
    "iso_strain",
}
REQUIRED_COLUMNS = {
    "elapsed_s",
    "automation_phase",
    "automation_target_value",
    "plateau_index",
    "strain_pct",
    "resistance_ohm",
}
CURRENT_COLUMNS = ("current_measured_mA", "current_set_mA")
MIN_POINTS_PER_TARGET = 2
MIN_POINTS_PER_ISO_CURRENT_TARGET = 3
STRAIN_BASELINE_RAW = "raw"
STRAIN_BASELINE_GLOBAL_MINIMUM = "global_minimum"
STRAIN_BASELINE_PER_TARGET_MINIMUM = "per_target_minimum"
STRAIN_BASELINE_MODES = {
    STRAIN_BASELINE_RAW,
    STRAIN_BASELINE_GLOBAL_MINIMUM,
    STRAIN_BASELINE_PER_TARGET_MINIMUM,
}
POWER_AXIS_ABSOLUTE_MW = "absolute_mw"
POWER_AXIS_NORMALIZED_MW_PER_CM = "normalized_mw_per_cm"
POWER_AXIS_MODES = {
    POWER_AXIS_ABSOLUTE_MW,
    POWER_AXIS_NORMALIZED_MW_PER_CM,
}


@dataclass(frozen=True)
class MiniDmaRun:
    path: Path
    measurement_path: Path
    frame: pd.DataFrame
    sample_name: str
    initial_length_mm: float | None = None
    wire_diameter_mm: float | None = None


@dataclass(frozen=True)
class CurrentSweepTargetSummary:
    stress_mpa: float
    load_g: float | None
    l0_mm: float | None
    max_current_mA: float | None
    max_strain_pct: float | None
    strain_at_max_current_pct: float | None
    as_current_mA: float | None = None
    af_current_mA: float | None = None
    ms_current_mA: float | None = None
    mf_current_mA: float | None = None


@dataclass(frozen=True)
class CurrentSweepBreakSummary:
    stress_mpa: float
    load_g: float | None
    current_mA: float | None
    reason: str = ""


@dataclass(frozen=True)
class CurrentSweepSummary:
    targets: tuple[CurrentSweepTargetSummary, ...]
    break_point: CurrentSweepBreakSummary | None = None


def resolve_measurement_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / MEASUREMENT_FILE
    if candidate.name.casefold() != MEASUREMENT_FILE.casefold():
        raise ValueError("Select a Mini DMA run folder or measurement.csv file.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"Mini DMA measurement file not found: {candidate}")
    return candidate


def looks_like_measurement_file(path: Path) -> bool:
    """Return true for CSV files with the required Mini DMA measurement shape."""
    candidate = Path(path)
    if candidate.name.casefold() != MEASUREMENT_FILE.casefold():
        return False
    if not candidate.exists() or not candidate.is_file():
        return False
    try:
        frame = pd.read_csv(candidate, nrows=1)
    except Exception:
        return False
    if frame.empty:
        return False
    if not REQUIRED_COLUMNS.issubset(frame.columns):
        return False
    return any(column in frame.columns for column in CURRENT_COLUMNS)


def load_run(path: Path) -> MiniDmaRun:
    measurement_path = resolve_measurement_path(path)
    frame = pd.read_csv(measurement_path)
    missing = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError("Missing Mini DMA columns: " + ", ".join(missing))
    current_column = _choose_current_column(frame)
    if current_column is None:
        raise ValueError("Missing usable current column.")

    cleaned = frame.copy()
    cleaned["current_mA"] = pd.to_numeric(cleaned[current_column], errors="coerce")
    for column in (
        "elapsed_s",
        "automation_target_value",
        "plateau_index",
        "strain_pct",
        "stress_mpa",
        "resistance_ohm",
        "current_set_mA",
        "current_measured_mA",
        "position_mm",
        "load_g",
        "current_l0_mm",
        "current_relative_position_mm",
        "current_relative_strain_pct",
    ):
        if column in cleaned.columns:
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned["automation_phase"] = cleaned["automation_phase"].astype(str)
    cleaned = cleaned.dropna(
        subset=["current_mA", "automation_target_value", "strain_pct", "resistance_ohm"]
    )
    if cleaned.empty:
        raise ValueError("No usable Mini DMA current-sweep rows found.")

    metadata = _metadata_for_run(measurement_path)
    sample_name = _sample_name_for_run(measurement_path)
    run_path = measurement_path.parent
    return MiniDmaRun(
        path=run_path,
        measurement_path=measurement_path,
        frame=cleaned.reset_index(drop=True),
        sample_name=sample_name,
        initial_length_mm=_initial_length_from_metadata(metadata),
        wire_diameter_mm=_wire_diameter_from_metadata(metadata),
    )


def is_iso_current_run(run: MiniDmaRun) -> bool:
    """Return true for constant-current stress/strain Mini DMA recipes."""
    modes = _run_recipe_mode_values(run)
    if modes.intersection(ISO_CURRENT_RECIPE_MODES):
        return True
    return _run_name_has_mode(run, "iso-current")


def supports_transition_review(run: MiniDmaRun) -> bool:
    """Return true when current-sweep transition extraction is meaningful."""
    if is_iso_current_run(run):
        return False
    if _run_name_has_mode(run, "iso-strain") or _run_name_has_mode(run, "iso-current"):
        return False
    modes = _run_recipe_mode_values(run)
    if modes.intersection(TRANSITION_REVIEW_UNSUPPORTED_RECIPE_MODES):
        return False
    if modes.intersection(TRANSITION_REVIEW_RECIPE_MODES):
        return True
    if _run_name_has_mode(run, "iso-stress") or "current-sweep" in _normalised_run_name(run):
        return True
    return bool(current_sweep_groups(run.frame, phases=SUMMARY_PHASES))


def iso_current_groups(run: MiniDmaRun) -> list[tuple[float, pd.DataFrame]]:
    """Group iso-current stress/strain rows by commanded current."""
    frame = run.frame
    if "stress_mpa" not in frame.columns:
        return []
    filtered = frame.copy()
    if "automation_phase" in filtered.columns:
        phase_mask = filtered["automation_phase"].isin(ISO_CURRENT_PHASES)
        if bool(phase_mask.any()):
            filtered = filtered.loc[phase_mask].copy()
    strain = _iso_current_strain_series(run, filtered)
    current = _iso_current_group_current(filtered)
    filtered["_mini_dma_iso_strain_pct"] = pd.to_numeric(strain, errors="coerce")
    filtered["_mini_dma_iso_current_mA"] = pd.to_numeric(current, errors="coerce")
    filtered = filtered.dropna(
        subset=[
            "_mini_dma_iso_current_mA",
            "_mini_dma_iso_strain_pct",
            "stress_mpa",
        ]
    )
    filtered = filtered[filtered["_mini_dma_iso_current_mA"].abs() > 0.0]
    if filtered.empty:
        return []

    rounded_current = filtered["_mini_dma_iso_current_mA"].round(6)
    groups: list[tuple[float, pd.DataFrame]] = []
    for current_mA, group in filtered.groupby(rounded_current, sort=True):
        usable = group.sort_values("elapsed_s", kind="stable").copy()
        usable = _drop_consecutive_duplicate_rows(
            usable,
            subset=["_mini_dma_iso_strain_pct", "stress_mpa", "load_g"],
        )
        if len(usable) < MIN_POINTS_PER_ISO_CURRENT_TARGET:
            continue
        x_values = pd.to_numeric(usable["_mini_dma_iso_strain_pct"], errors="coerce")
        y_values = pd.to_numeric(usable["stress_mpa"], errors="coerce")
        if x_values.nunique(dropna=True) < 2 or y_values.nunique(dropna=True) < 2:
            continue
        groups.append((float(current_mA), usable.reset_index(drop=True)))
    return groups


def current_sweep_groups(
    frame: pd.DataFrame,
    *,
    phases: Collection[str] | None = None,
) -> list[tuple[float, pd.DataFrame]]:
    phase_filter = PLOT_PHASES if phases is None else set(phases)
    filtered = frame[frame["automation_phase"].isin(phase_filter)].copy()
    if filtered.empty:
        filtered = frame.copy()
    filtered = filtered.dropna(
        subset=["current_mA", "automation_target_value", "strain_pct", "resistance_ohm"]
    )
    if filtered.empty:
        return []

    groups: list[tuple[float, pd.DataFrame]] = []
    for target, group in filtered.groupby("automation_target_value", sort=True):
        target_value = float(target)
        for subgroup in _split_current_target_group(group):
            usable = subgroup.sort_values("elapsed_s", kind="stable").copy()
            usable = usable[usable["current_mA"].abs() > 0.0]
            usable = _drop_consecutive_duplicate_rows(
                usable,
                subset=["current_mA", "strain_pct", "resistance_ohm"],
            )
            if len(usable) < MIN_POINTS_PER_TARGET:
                continue
            groups.append((target_value, usable.reset_index(drop=True)))
    return groups


def _split_current_target_group(group: pd.DataFrame) -> list[pd.DataFrame]:
    if "plateau_index" not in group.columns:
        return [group]
    plateau = pd.to_numeric(group["plateau_index"], errors="coerce")
    if plateau.nunique(dropna=False) <= 1:
        return [group]

    keyed = group.copy()
    keyed["_mini_dma_plateau_group"] = plateau.map(
        lambda value: "__first_overheating__" if pd.isna(value) else f"{float(value):.12g}"
    )
    subgroups: list[pd.DataFrame] = []
    for _key, subgroup in keyed.groupby("_mini_dma_plateau_group", sort=False):
        subgroups.append(subgroup.drop(columns=["_mini_dma_plateau_group"]))
    return subgroups


def make_strain_current_figure(
    run: MiniDmaRun,
    *,
    zero_minimum_strain: bool = False,
    strain_baseline_mode: str | None = None,
    show_power_top_axis: bool = False,
    power_axis_mode: str = POWER_AXIS_NORMALIZED_MW_PER_CM,
) -> Figure:
    baseline_mode = _normalise_strain_baseline_mode(
        strain_baseline_mode,
        zero_minimum_strain=zero_minimum_strain,
    )
    return _make_current_figure(
        run,
        y_column="strain_pct",
        y_label="Strain [%]",
        title_suffix="Strain vs Current",
        strain_baseline_mode=baseline_mode,
        show_power_top_axis=show_power_top_axis,
        power_axis_mode=power_axis_mode,
    )


def make_resistance_current_figure(
    run: MiniDmaRun,
    *,
    show_power_top_axis: bool = False,
    power_axis_mode: str = POWER_AXIS_NORMALIZED_MW_PER_CM,
) -> Figure:
    return _make_current_figure(
        run,
        y_column="resistance_ohm",
        y_label="Resistance [Ohm]",
        title_suffix="Resistance vs Current",
        filter_resistance_outliers=True,
        show_power_top_axis=show_power_top_axis,
        power_axis_mode=power_axis_mode,
    )


def make_iso_current_figure(run: MiniDmaRun) -> Figure:
    groups = iso_current_groups(run)
    if not groups:
        raise ValueError("No iso-current stress/strain groups with enough points.")

    fig = Figure(figsize=(8.0, 5.0), constrained_layout=True)
    ax = fig.add_subplot(111)
    displacement_points: list[tuple[float, float]] = []
    load_points: list[tuple[float, float]] = []
    l0_values: list[float] = []
    for current_mA, group in groups:
        strain = pd.to_numeric(group["_mini_dma_iso_strain_pct"], errors="coerce")
        stress = pd.to_numeric(group["stress_mpa"], errors="coerce")
        ax.plot(
            strain.to_numpy(dtype=float),
            stress.to_numpy(dtype=float),
            label=_format_current_density_label(run, current_mA),
            linewidth=1.4,
            marker="o",
            markersize=3.5,
        )
        displacement = _iso_current_displacement_series(run, group, strain)
        for x_value, displacement_value in zip(
            strain.tolist(),
            displacement.tolist(),
            strict=False,
        ):
            if pd.notna(x_value) and pd.notna(displacement_value):
                displacement_points.append((float(x_value), float(displacement_value)))
        if "load_g" in group.columns:
            load = pd.to_numeric(group["load_g"], errors="coerce")
            for stress_value, load_value in zip(stress.tolist(), load.tolist(), strict=False):
                if pd.notna(stress_value) and pd.notna(load_value):
                    load_points.append((float(stress_value), float(load_value)))
        l0_values.extend(_iso_current_l0_values(group))

    ax.set_title(f"{run.sample_name} - Iso-current Stress vs Strain")
    ax.set_xlabel(_iso_current_strain_axis_label(run, l0_values))
    ax.set_ylabel(_iso_current_stress_axis_label(run))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9, title="Current / current density", title_fontsize=9)
    _add_displacement_top_axis(ax, displacement_points)
    _add_load_right_axis(ax, load_points)
    return fig


def _make_current_figure(
    run: MiniDmaRun,
    *,
    y_column: str,
    y_label: str,
    title_suffix: str,
    filter_resistance_outliers: bool = False,
    strain_baseline_mode: str = STRAIN_BASELINE_RAW,
    show_power_top_axis: bool = False,
    power_axis_mode: str = POWER_AXIS_NORMALIZED_MW_PER_CM,
) -> Figure:
    groups = current_sweep_groups(run.frame)
    if not groups:
        raise ValueError("No current-sweep target groups with enough points.")

    fig = Figure(figsize=(8.0, 5.0), constrained_layout=True)
    ax = fig.add_subplot(111)
    power_currents: list[float] = []
    power_resistances: list[float] = []
    global_l0_mm = (
        _global_minimum_length_mm(run, [group for _target, group in groups])
        if y_column == "strain_pct" and strain_baseline_mode == STRAIN_BASELINE_GLOBAL_MINIMUM
        else None
    )
    global_strain_min = (
        _global_minimum_strain_pct(run, [group for _target, group in groups])
        if y_column == "strain_pct" and strain_baseline_mode == STRAIN_BASELINE_GLOBAL_MINIMUM
        else None
    )
    plotted_groups: list[tuple[float, pd.DataFrame]] = []
    for target, group in groups:
        if filter_resistance_outliers:
            group = _drop_resistance_outliers(group)
        if len(group) < MIN_POINTS_PER_TARGET:
            continue
        plotted_groups.append((target, group))

    for target, group in plotted_groups:
        if show_power_top_axis:
            power_currents.extend(group["current_mA"].to_numpy(dtype=float).tolist())
            power_resistances.extend(group["resistance_ohm"].to_numpy(dtype=float).tolist())
        y_values = group[y_column].to_numpy(dtype=float)
        if y_column == "strain_pct" and len(y_values):
            if strain_baseline_mode == STRAIN_BASELINE_PER_TARGET_MINIMUM:
                y_values = _strain_from_trace_minimum_length(run, group)
            elif strain_baseline_mode == STRAIN_BASELINE_GLOBAL_MINIMUM:
                y_values = _strain_from_global_minimum_length(
                    run,
                    group,
                    global_l0_mm,
                    global_strain_min,
                )
        is_first_overheating = _is_first_overheating_group(run, target, group)
        ax.plot(
            group["current_mA"].to_numpy(dtype=float),
            y_values,
            label=_format_plot_target_label(run, target, group),
            linewidth=1.8 if is_first_overheating else 1.4,
            linestyle="--" if is_first_overheating else "-",
            marker="D" if is_first_overheating else "o",
            markersize=4.2 if is_first_overheating else 3.5,
        )
    ax.set_title(f"{run.sample_name} - {title_suffix}")
    ax.set_xlabel(_current_axis_label(run, [group for _target, group in plotted_groups]))
    if y_column == "strain_pct":
        y_label = _strain_axis_label(
            run,
            strain_baseline_mode,
            global_l0_mm=global_l0_mm,
        )
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    if show_power_top_axis:
        power_label, power_scale = power_axis_label_and_scale(run, power_axis_mode)
        add_power_top_axis(
            ax,
            power_currents,
            power_resistances,
            label=power_label,
            power_scale=power_scale,
            label_size=11,
            tick_size=9,
        )
    ax.legend(loc="best", fontsize=9, title="Stress / load", title_fontsize=9)
    return fig


def build_plot_frame(run: MiniDmaRun, *, y_column: str) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for target, group in current_sweep_groups(run.frame):
        if y_column == "resistance_ohm":
            group = _drop_resistance_outliers(group)
        if len(group) < MIN_POINTS_PER_TARGET:
            continue
        token = _target_token(target)
        columns[f"{token}_current_mA"] = pd.Series(group["current_mA"].to_numpy(dtype=float))
        columns[f"{token}_{y_column}"] = pd.Series(group[y_column].to_numpy(dtype=float))
    return pd.DataFrame(columns)


def power_axis_points(run: MiniDmaRun, *, filter_resistance_outliers: bool = True) -> tuple[list[float], list[float]]:
    currents: list[float] = []
    resistances: list[float] = []
    for _target, group in current_sweep_groups(run.frame):
        if filter_resistance_outliers:
            group = _drop_resistance_outliers(group)
        if len(group) < MIN_POINTS_PER_TARGET:
            continue
        currents.extend(group["current_mA"].to_numpy(dtype=float).tolist())
        resistances.extend(group["resistance_ohm"].to_numpy(dtype=float).tolist())
    return currents, resistances


def power_axis_label_and_scale(
    run: MiniDmaRun,
    mode: str = POWER_AXIS_NORMALIZED_MW_PER_CM,
) -> tuple[str, float]:
    if mode == POWER_AXIS_NORMALIZED_MW_PER_CM:
        length_mm = run.initial_length_mm
        if length_mm is not None and math.isfinite(length_mm) and length_mm > 0.0:
            return "Power/cm [mW/cm]", 10.0 / length_mm
    return "Power [mW]", 1.0


def strain_from_trace_minimum_length(run: MiniDmaRun, group: pd.DataFrame) -> pd.Series:
    return pd.Series(_strain_from_trace_minimum_length(run, group), index=group.index)


def strain_from_global_minimum_length(
    run: MiniDmaRun,
    groups: Iterable[pd.DataFrame],
) -> list[pd.Series]:
    group_list = list(groups)
    l0_mm = _global_minimum_length_mm(run, group_list)
    global_strain_min = _global_minimum_strain_pct(run, group_list)
    return [
        pd.Series(
            _strain_from_global_minimum_length(run, group, l0_mm, global_strain_min),
            index=group.index,
        )
        for group in group_list
    ]


def summarize_current_sweep(
    run: MiniDmaRun,
    *,
    voltage_limit_v: float | None = None,
) -> CurrentSweepSummary:
    target_summaries: list[CurrentSweepTargetSummary] = []
    for target, group in current_sweep_groups(run.frame, phases=SUMMARY_PHASES):
        strain = _strain_from_trace_minimum_length(run, group)
        current = pd.to_numeric(group["current_mA"], errors="coerce")
        max_current_mA: float | None = None
        strain_at_max_current: float | None = None
        if current.notna().any():
            max_index = current.idxmax(skipna=True)
            max_current_mA = float(current.loc[max_index])
            strain_at_max_current = float(strain.loc[max_index])
        max_strain_pct: float | None = None
        if strain.notna().any():
            max_strain_pct = float(strain.max(skipna=True))
        l0_mm = _group_l0_mm(run, group)
        target_summaries.append(
            CurrentSweepTargetSummary(
                stress_mpa=float(target),
                load_g=_load_g_from_stress_mpa(run, float(target)),
                l0_mm=l0_mm,
                max_current_mA=max_current_mA,
                max_strain_pct=max_strain_pct,
                strain_at_max_current_pct=strain_at_max_current,
                **_transition_currents_for_group(strain, group),
            )
        )
    return CurrentSweepSummary(
        targets=tuple(target_summaries),
        break_point=_detect_break_point(run, voltage_limit_v=voltage_limit_v),
    )


def format_current_sweep_strain_summary(summary: CurrentSweepSummary) -> list[str]:
    lines: list[str] = []
    for target in summary.targets:
        strain = target.strain_at_max_current_pct
        current = target.max_current_mA
        if strain is None or current is None:
            continue
        label = _format_target_summary_label(target.stress_mpa, target.load_g)
        lines.append(
            f"{label}: {_format_compact_number(strain, max_decimals=2)}% "
            f"@ {_format_compact_number(current, max_decimals=0)} mA"
        )
    return lines


def format_current_sweep_break_summary(summary: CurrentSweepSummary) -> str:
    break_point = summary.break_point
    if break_point is None:
        return ""
    label = _format_target_summary_label(break_point.stress_mpa, break_point.load_g)
    if break_point.current_mA is None:
        return f"{label}: break detected"
    return f"{label} @ {_format_compact_number(break_point.current_mA, max_decimals=0)} mA"


def format_current_sweep_transition_summary(summary: CurrentSweepSummary) -> list[str]:
    lines: list[str] = []
    for target in summary.targets:
        values = (
            ("As", target.as_current_mA),
            ("Af", target.af_current_mA),
            ("Ms", target.ms_current_mA),
            ("Mf", target.mf_current_mA),
        )
        if not all(value is not None for _label, value in values):
            continue
        label = _format_target_summary_label(target.stress_mpa, target.load_g)
        parts = [
            f"{name} {_format_compact_number(float(value), max_decimals=0)} mA"
            for name, value in values
            if value is not None
        ]
        lines.append(f"{label}: " + ", ".join(parts))
    return lines


def _drop_resistance_outliers(group: pd.DataFrame) -> pd.DataFrame:
    if "resistance_ohm" not in group or len(group) < 6:
        return group
    resistance = pd.to_numeric(group["resistance_ohm"], errors="coerce")
    q1 = float(resistance.quantile(0.25))
    q3 = float(resistance.quantile(0.75))
    iqr = q3 - q1
    if not pd.notna(iqr) or iqr <= 0.0:
        return group
    lower = max(0.0, q1 - (3.0 * iqr))
    upper = q3 + (3.0 * iqr)
    mask = resistance.between(lower, upper)
    filtered = group.loc[mask].copy()
    if len(filtered) < MIN_POINTS_PER_TARGET:
        return group
    return filtered.reset_index(drop=True)


def _drop_consecutive_duplicate_rows(
    frame: pd.DataFrame,
    *,
    subset: Sequence[str],
) -> pd.DataFrame:
    available = [column for column in subset if column in frame.columns]
    if not available or len(frame.index) < 2:
        return frame
    comparable = frame[available]
    duplicate_previous = comparable.eq(comparable.shift()).all(axis=1)
    return frame.loc[~duplicate_previous].copy()


def _strain_from_trace_minimum_length(run: MiniDmaRun, group: pd.DataFrame) -> pd.Series:
    """Recalculate strain with each curve's shortest measured length as l0."""
    length_mm = _length_trace_mm(run, group)
    if length_mm is not None and length_mm.notna().any():
        l0_mm = float(length_mm.min(skipna=True))
        if pd.notna(l0_mm) and l0_mm > 0.0:
            return (length_mm - l0_mm) / l0_mm * 100.0

    strain_pct = pd.to_numeric(group["strain_pct"], errors="coerce")
    return strain_pct - strain_pct.min(skipna=True)


def _group_l0_mm(run: MiniDmaRun, group: pd.DataFrame) -> float | None:
    length_mm = _length_trace_mm(run, group)
    if length_mm is None or not length_mm.notna().any():
        return None
    l0_mm = float(length_mm.min(skipna=True))
    if pd.notna(l0_mm) and l0_mm > 0.0:
        return l0_mm
    return None


def _strain_from_global_minimum_length(
    run: MiniDmaRun,
    group: pd.DataFrame,
    global_l0_mm: float | None,
    global_strain_min: float | None,
) -> pd.Series:
    length_mm = _length_trace_mm(run, group)
    if length_mm is not None and length_mm.notna().any():
        if global_l0_mm is not None and pd.notna(global_l0_mm) and global_l0_mm > 0.0:
            return (length_mm - global_l0_mm) / global_l0_mm * 100.0

    strain_pct = pd.to_numeric(group["strain_pct"], errors="coerce")
    if global_strain_min is None:
        global_strain_min = float(strain_pct.min(skipna=True))
    return strain_pct - global_strain_min


def _length_trace_mm(run: MiniDmaRun, group: pd.DataFrame) -> pd.Series | None:
    if "position_mm" in group.columns and run.initial_length_mm is not None:
        position = pd.to_numeric(group["position_mm"], errors="coerce")
        if position.notna().any():
            return run.initial_length_mm + position

    strain_pct = pd.to_numeric(group["strain_pct"], errors="coerce")
    if run.initial_length_mm is not None and strain_pct.notna().any():
        return run.initial_length_mm * (1.0 + strain_pct / 100.0)
    return None


def _global_minimum_length_mm(run: MiniDmaRun, groups: Iterable[pd.DataFrame]) -> float | None:
    minima: list[float] = []
    for group in groups:
        length_mm = _length_trace_mm(run, group)
        if length_mm is None or not length_mm.notna().any():
            continue
        value = float(length_mm.min(skipna=True))
        if pd.notna(value) and value > 0.0:
            minima.append(value)
    if not minima:
        return None
    return min(minima)


def _global_minimum_strain_pct(run: MiniDmaRun, groups: Iterable[pd.DataFrame]) -> float | None:
    minima: list[float] = []
    for group in groups:
        strain_pct = pd.to_numeric(group["strain_pct"], errors="coerce")
        if strain_pct.notna().any():
            value = float(strain_pct.min(skipna=True))
            if pd.notna(value):
                minima.append(value)
    if not minima:
        return None
    return min(minima)


def _normalise_strain_baseline_mode(
    mode: str | None,
    *,
    zero_minimum_strain: bool = False,
) -> str:
    if isinstance(mode, str) and mode in STRAIN_BASELINE_MODES:
        return mode
    if zero_minimum_strain:
        return STRAIN_BASELINE_PER_TARGET_MINIMUM
    return STRAIN_BASELINE_RAW


def _strain_axis_label(
    run: MiniDmaRun,
    mode: str,
    *,
    global_l0_mm: float | None = None,
) -> str:
    if mode == STRAIN_BASELINE_PER_TARGET_MINIMUM:
        return "Strain [%] (per-curve l₀)"
    l0_mm = global_l0_mm if mode == STRAIN_BASELINE_GLOBAL_MINIMUM else run.initial_length_mm
    if l0_mm is None or not pd.notna(l0_mm) or l0_mm <= 0.0:
        return "Strain [%]"
    return f"Strain [%] (l₀ = {_format_compact_number(l0_mm, max_decimals=1)} mm)"


def _metadata_recipe_mode_values(payload: dict[str, object]) -> set[str]:
    values: set[str] = set()
    for key in ("recipe_mode", "mode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            values.add(value.strip().casefold())
    recipe = payload.get("recipe")
    if isinstance(recipe, dict):
        for key in ("recipe_mode", "mode", "type"):
            value = recipe.get(key)
            if isinstance(value, str) and value.strip():
                values.add(value.strip().casefold())
        for key, section in recipe.items():
            if isinstance(key, str) and key.strip():
                values.add(key.strip().casefold())
            if isinstance(section, dict):
                nested_mode = section.get("recipe_mode") or section.get("mode") or section.get("type")
                if isinstance(nested_mode, str) and nested_mode.strip():
                    values.add(nested_mode.strip().casefold())
    for key, section in payload.items():
        if isinstance(key, str) and "iso-current" in key.casefold():
            values.add("iso-current")
        if isinstance(section, dict):
            nested_mode = section.get("recipe_mode") or section.get("mode") or section.get("type")
            if isinstance(nested_mode, str) and nested_mode.strip():
                values.add(nested_mode.strip().casefold())
    return values


def _run_recipe_mode_values(run: MiniDmaRun) -> set[str]:
    values: set[str] = set()
    frame = run.frame
    if "recipe_mode" in frame.columns:
        values.update(
            str(value).strip().casefold()
            for value in frame["recipe_mode"].dropna().unique().tolist()
            if str(value).strip()
        )
    metadata = _metadata_for_run(run.measurement_path)
    values.update(_metadata_recipe_mode_values(metadata))
    return values


def _normalised_run_name(run: MiniDmaRun) -> str:
    return run.path.name.casefold().replace("_", "-")


def _run_name_has_mode(run: MiniDmaRun, mode: str) -> bool:
    return mode.casefold().replace("_", "-") in _normalised_run_name(run)


def _has_iso_current_columns(frame: pd.DataFrame) -> bool:
    return bool(
        {"current_relative_strain_pct", "current_l0_mm"}.intersection(frame.columns)
    )


def _iso_current_strain_series(run: MiniDmaRun, frame: pd.DataFrame) -> pd.Series:
    if "current_relative_strain_pct" in frame.columns:
        values = pd.to_numeric(frame["current_relative_strain_pct"], errors="coerce")
        if values.notna().any():
            return values
    if {"current_relative_position_mm", "current_l0_mm"}.issubset(frame.columns):
        position = pd.to_numeric(frame["current_relative_position_mm"], errors="coerce")
        l0 = pd.to_numeric(frame["current_l0_mm"], errors="coerce")
        values = position / l0 * 100.0
        if values.notna().any():
            return values
    if "strain_pct" in frame.columns:
        strain = pd.to_numeric(frame["strain_pct"], errors="coerce")
        if strain.notna().any():
            return strain - strain.min(skipna=True)
    return pd.Series([math.nan] * len(frame.index), index=frame.index)


def _iso_current_group_current(frame: pd.DataFrame) -> pd.Series:
    for column in ("current_set_mA", "current_mA", "current_measured_mA"):
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().any() and values.abs().sum() > 0.0:
            return values
    return pd.Series([math.nan] * len(frame.index), index=frame.index)


def _iso_current_l0_values(group: pd.DataFrame) -> list[float]:
    values: list[float] = []
    if "current_l0_mm" in group.columns:
        series = pd.to_numeric(group["current_l0_mm"], errors="coerce").dropna()
        values.extend(float(value) for value in series.tolist() if float(value) > 0.0)
    return values


def _iso_current_displacement_series(
    run: MiniDmaRun,
    group: pd.DataFrame,
    strain: pd.Series,
) -> pd.Series:
    if "current_relative_position_mm" in group.columns:
        values = pd.to_numeric(group["current_relative_position_mm"], errors="coerce")
        if values.notna().any():
            return values
    if "current_l0_mm" in group.columns:
        l0 = pd.to_numeric(group["current_l0_mm"], errors="coerce")
        values = pd.to_numeric(strain, errors="coerce") / 100.0 * l0
        if values.notna().any():
            return values
    if run.initial_length_mm is not None and run.initial_length_mm > 0.0:
        return pd.to_numeric(strain, errors="coerce") / 100.0 * run.initial_length_mm
    if "position_mm" in group.columns:
        values = pd.to_numeric(group["position_mm"], errors="coerce")
        if values.notna().any():
            return values - values.min(skipna=True)
    return pd.Series([math.nan] * len(group.index), index=group.index)


def _iso_current_strain_axis_label(run: MiniDmaRun, l0_values: Sequence[float]) -> str:
    finite_values = [float(value) for value in l0_values if math.isfinite(float(value))]
    if finite_values:
        minimum = min(finite_values)
        maximum = max(finite_values)
        if math.isclose(minimum, maximum, rel_tol=1e-6, abs_tol=1e-6):
            return f"Strain [%] (l\u2080 = {_format_compact_number(minimum, max_decimals=1)} mm)"
        return "Strain [%] (per-current l\u2080)"
    return _strain_axis_label(run, STRAIN_BASELINE_RAW)


def _iso_current_stress_axis_label(run: MiniDmaRun) -> str:
    if run.wire_diameter_mm is None or run.wire_diameter_mm <= 0.0:
        return "Stress [MPa]"
    diameter_um = run.wire_diameter_mm * 1000.0
    return f"Stress [MPa] (d = {_format_compact_number(diameter_um)} \u00b5m)"


def _format_current_density_label(run: MiniDmaRun, current_mA: float) -> str:
    current_label = f"{_format_compact_number(current_mA, max_decimals=1)} mA"
    area_mm2 = _wire_area_mm2(run)
    if area_mm2 is None:
        return current_label
    current_density = (current_mA / 1000.0) / area_mm2
    return (
        f"{current_label} / "
        f"{_format_compact_number(current_density, max_decimals=0)} A/mm\u00b2"
    )


def _add_displacement_top_axis(ax: object, points: Sequence[tuple[float, float]]) -> None:
    if not points:
        return
    x_values, displacement_values = _averaged_axis_lookup(points)
    if len(set(x_values)) < 2:
        return
    top_ax = ax.twiny()
    top_ax.set_xlim(ax.get_xlim())
    ticks = [
        float(tick)
        for tick in ax.get_xticks()
        if x_values[0] <= float(tick) <= x_values[-1]
    ]
    if not ticks:
        ticks = [
            x_values[0],
            x_values[len(x_values) // 2],
            x_values[-1],
        ]
    top_ax.set_xticks(ticks)
    labels: list[str] = []
    for tick in ticks:
        displacement = _interpolate_axis_value(tick, x_values, displacement_values)
        labels.append(_format_compact_number(displacement, max_decimals=3))
    top_ax.set_xticklabels(labels)
    top_ax.set_xlabel("Displacement [mm]")


def _add_load_right_axis(ax: object, points: Sequence[tuple[float, float]]) -> None:
    if not points:
        return
    stress_values, load_values = _averaged_axis_lookup(points)
    if len(set(stress_values)) < 2:
        return
    right_ax = ax.twinx()
    right_ax.set_ylim(ax.get_ylim())
    ticks = [
        float(tick)
        for tick in ax.get_yticks()
        if stress_values[0] <= float(tick) <= stress_values[-1]
    ]
    if not ticks:
        ticks = [
            stress_values[0],
            stress_values[len(stress_values) // 2],
            stress_values[-1],
        ]
    right_ax.set_yticks(ticks)
    labels: list[str] = []
    for tick in ticks:
        load = _interpolate_axis_value(tick, stress_values, load_values)
        labels.append(_format_compact_number(load, max_decimals=2))
    right_ax.set_yticklabels(labels)
    right_ax.set_ylabel("Load [g]")


def _averaged_axis_lookup(points: Sequence[tuple[float, float]]) -> tuple[list[float], list[float]]:
    grouped: dict[float, list[float]] = {}
    for axis_value, display_value in points:
        if not math.isfinite(axis_value) or not math.isfinite(display_value):
            continue
        grouped.setdefault(float(axis_value), []).append(float(display_value))
    x_values = sorted(grouped)
    y_values = [float(np.mean(grouped[x_value])) for x_value in x_values]
    return x_values, y_values


def _interpolate_axis_value(
    x_value: float,
    x_values: Sequence[float],
    y_values: Sequence[float],
) -> float:
    if x_value <= x_values[0]:
        return y_values[0]
    if x_value >= x_values[-1]:
        return y_values[-1]
    for index in range(1, len(x_values)):
        left_x = x_values[index - 1]
        right_x = x_values[index]
        if x_value > right_x:
            continue
        left_y = y_values[index - 1]
        right_y = y_values[index]
        if math.isclose(right_x, left_x, abs_tol=1e-12):
            return right_y
        ratio = (x_value - left_x) / (right_x - left_x)
        return left_y + ratio * (right_y - left_y)
    return y_values[-1]


def _choose_current_column(frame: pd.DataFrame) -> str | None:
    for column in CURRENT_COLUMNS:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if not values.empty and values.abs().sum() > 0.0:
            return column
    return None


def _metadata_for_run(measurement_path: Path) -> dict[str, object]:
    metadata_path = measurement_path.with_name("metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _initial_length_from_metadata(payload: dict[str, object]) -> float | None:
    value = payload.get("initial_length_mm")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


def _wire_diameter_from_metadata(payload: dict[str, object]) -> float | None:
    for key in ("wire_diameter_mm", "diameter_mm"):
        value = payload.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0.0:
            return parsed
    return None


def _sample_name_for_run(measurement_path: Path) -> str:
    payload = _metadata_for_run(measurement_path)
    name = payload.get("sample_name")
    if isinstance(name, str) and name.strip():
        return name.strip().replace("/", "_")
    return measurement_path.parent.name


def _format_stress_label(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)} MPa"
    return f"{value:g} MPa"


def _format_target_label(run: MiniDmaRun, value: float, *, compact: bool = False) -> str:
    stress_label = _format_stress_label(value)
    if compact:
        stress_label = stress_label.replace(" ", "")
    load_g = _load_g_from_stress_mpa(run, value)
    if load_g is None:
        return stress_label
    load_label = f"{_format_compact_number(load_g)}g" if compact else f"{_format_compact_number(load_g)} g"
    return f"{stress_label} / {load_label}"


def _format_plot_target_label(run: MiniDmaRun, value: float, group: pd.DataFrame) -> str:
    if _is_first_overheating_group(run, value, group):
        return f"1st: {_format_target_label(run, value, compact=True)}"
    return _format_target_label(run, value)


def first_overheating_target_mpa(run: MiniDmaRun) -> float | None:
    metadata = _metadata_for_run(run.measurement_path)
    sections: list[dict[str, object]] = []
    for key in ("controlled_current_sweep", "current_sweep"):
        section = metadata.get(key)
        if isinstance(section, dict):
            sections.append(section)
    recipe = metadata.get("recipe")
    if isinstance(recipe, dict):
        section = recipe.get("current_sweep")
        if isinstance(section, dict):
            sections.append(section)

    for section in sections:
        enabled = section.get("first_overheating")
        if isinstance(enabled, str):
            enabled_bool = enabled.strip().casefold() in {"1", "true", "yes", "on"}
        else:
            enabled_bool = bool(enabled)
        if not enabled_bool:
            continue
        target = _dict_float(section, "first_overheating_target_mpa")
        if target is not None:
            return target
    return None


def _is_first_overheating_group(run: MiniDmaRun, target: float, group: pd.DataFrame) -> bool:
    first_target = first_overheating_target_mpa(run)
    if first_target is None:
        return False
    if not math.isclose(float(target), first_target, rel_tol=1e-9, abs_tol=1e-6):
        return False
    if "plateau_index" not in group.columns:
        return True
    plateau = pd.to_numeric(group["plateau_index"], errors="coerce")
    return not plateau.notna().any()


def _load_g_from_stress_mpa(run: MiniDmaRun, stress_mpa: float) -> float | None:
    area_mm2 = _wire_area_mm2(run)
    if area_mm2 is None:
        return None
    return stress_mpa * area_mm2 / 9.80665 * 1000.0


def _format_target_summary_label(stress_mpa: float, load_g: float | None) -> str:
    stress_label = _format_stress_label(stress_mpa)
    if load_g is None:
        return stress_label
    return f"{stress_label} / {_format_compact_number(load_g)} g"


def _wire_area_mm2(run: MiniDmaRun) -> float | None:
    if run.wire_diameter_mm is None or run.wire_diameter_mm <= 0.0:
        return None
    return math.pi * (run.wire_diameter_mm**2) / 4.0


def _current_axis_label(run: MiniDmaRun, groups: Iterable[pd.DataFrame]) -> str:
    area_mm2 = _wire_area_mm2(run)
    if area_mm2 is None:
        return "Current [mA]"

    max_current_mA: float | None = None
    for group in groups:
        current = pd.to_numeric(group["current_mA"], errors="coerce")
        if current.notna().any():
            group_max = float(current.max(skipna=True))
            if pd.notna(group_max):
                max_current_mA = (
                    group_max if max_current_mA is None else max(max_current_mA, group_max)
                )
    if max_current_mA is None:
        return "Current [mA]"

    current_density = (max_current_mA / 1000.0) / area_mm2
    diameter_um = run.wire_diameter_mm * 1000.0
    return (
        "Current [mA] "
        f"({_format_compact_number(max_current_mA, max_decimals=0)} mA = "
        f"{_format_compact_number(current_density, max_decimals=0)} A/mm², "
        f"d = {_format_compact_number(diameter_um)} µm)"
    )


def _format_compact_number(value: float, *, max_decimals: int = 2) -> str:
    rounded = round(float(value), max_decimals)
    if math.isclose(rounded, round(rounded), abs_tol=0.5 * (10**-max_decimals)):
        return str(int(round(rounded)))
    return f"{rounded:.{max_decimals}f}".rstrip("0").rstrip(".")


def _transition_currents_for_group(
    strain_pct: pd.Series,
    group: pd.DataFrame,
) -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "as_current_mA": None,
        "af_current_mA": None,
        "ms_current_mA": None,
        "mf_current_mA": None,
    }
    heating, cooling = _split_current_sweep_legs(group)
    cooling_after_slope_hint = _high_current_cooling_slope(cooling, strain_pct)
    heating_fit = _fit_current_transition(
        heating,
        strain_pct,
        after_slope_hint=cooling_after_slope_hint,
        prefer_horizontal_after=True,
    )
    if heating_fit is not None:
        result["as_current_mA"] = heating_fit.start_x
        result["af_current_mA"] = heating_fit.finish_x
    cooling_fit = _fit_current_transition(cooling, strain_pct)
    if cooling_fit is not None:
        result["mf_current_mA"] = cooling_fit.start_x
        result["ms_current_mA"] = cooling_fit.finish_x
    return result


def _split_current_sweep_legs(group: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if group.empty or "current_mA" not in group.columns:
        return group.iloc[0:0], group.iloc[0:0]
    current = pd.to_numeric(group["current_mA"], errors="coerce")
    if not current.notna().any():
        return group.iloc[0:0], group.iloc[0:0]
    max_index = current.idxmax(skipna=True)
    index_positions = {index: position for position, index in enumerate(group.index)}
    max_position = index_positions.get(max_index)
    if max_position is None:
        return group.iloc[0:0], group.iloc[0:0]
    heating = group.iloc[: max_position + 1].copy()
    cooling = group.iloc[max_position:].copy()
    if len(heating.index) < 3:
        heating = group.iloc[0:0]
    if len(cooling.index) < 3:
        cooling = group.iloc[0:0]
    return heating, cooling


def _fit_current_transition(
    group: pd.DataFrame,
    strain_pct: pd.Series,
    *,
    after_slope_hint: float | None = None,
    prefer_horizontal_after: bool = False,
) -> TangentTransitionFit | None:
    if group.empty or len(group.index) < 18:
        return None
    strain = strain_pct.reindex(group.index)
    generic = fit_tangent_transition(
        pd.to_numeric(group["current_mA"], errors="coerce"),
        pd.to_numeric(strain, errors="coerce"),
        min_segment_points=6,
        # The fitter sorts by current. Heating strain must drop as current rises,
        # and cooling strain rises as current falls, so both are negative here.
        transition_slope_sign=-1,
    )
    if not prefer_horizontal_after:
        return generic
    if generic is not None and _mini_dma_after_fit_is_flat_enough(
        generic,
        after_slope_hint=after_slope_hint,
    ):
        return generic
    anchored = _fit_current_transition_with_suffix_after(
        group,
        strain,
        after_slope_hint=after_slope_hint,
    )
    return anchored if anchored is not None else generic


def _high_current_cooling_slope(
    cooling: pd.DataFrame,
    strain_pct: pd.Series,
) -> float | None:
    if cooling.empty or "current_mA" not in cooling.columns or len(cooling.index) < 6:
        return None
    head = cooling.iloc[: min(len(cooling.index), 12)]
    strain = strain_pct.reindex(head.index)
    fit = _mini_dma_line_fit(
        pd.to_numeric(head["current_mA"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(strain, errors="coerce").to_numpy(dtype=float),
    )
    if fit is None or not math.isfinite(fit.slope):
        return None
    return float(fit.slope)


def _mini_dma_after_fit_is_flat_enough(
    fit: TangentTransitionFit,
    *,
    after_slope_hint: float | None = None,
) -> bool:
    transition_slope = abs(float(fit.transition.slope))
    hint = (
        abs(float(after_slope_hint))
        if isinstance(after_slope_hint, (int, float)) and math.isfinite(float(after_slope_hint))
        else 0.0
    )
    limit = max(0.01, hint * 2.5, transition_slope * 0.35)
    return abs(float(fit.after.slope)) <= limit


def _fit_current_transition_with_suffix_after(
    group: pd.DataFrame,
    strain_pct: pd.Series,
    *,
    after_slope_hint: float | None = None,
) -> TangentTransitionFit | None:
    if group.empty or len(group.index) < 18 or "current_mA" not in group.columns:
        return None
    strain = strain_pct.reindex(group.index)
    x, y = _mini_dma_clean_xy(
        pd.to_numeric(group["current_mA"], errors="coerce"),
        pd.to_numeric(strain, errors="coerce"),
    )
    min_points = 6
    if len(x) < min_points * 3:
        return None
    if len(x) > 240:
        indices = np.linspace(0, len(x) - 1, 240).round().astype(int)
        x = x[indices]
        y = y[indices]
    n = len(x)
    lower = float(np.min(x))
    upper = float(np.max(x))
    scan_width = upper - lower
    if scan_width <= 0.0:
        return None
    hint = (
        abs(float(after_slope_hint))
        if isinstance(after_slope_hint, (int, float)) and math.isfinite(float(after_slope_hint))
        else 0.0
    )
    best: tuple[float, TangentTransitionFit] | None = None
    first_after_start = max(min_points * 2, n - (min_points * 5))
    for right_start in range(first_after_start, n - min_points + 1):
        after = _mini_dma_line_fit(x[right_start:], y[right_start:])
        if after is None:
            continue
        for left_end in range(min_points, right_start - min_points + 1):
            before = _mini_dma_line_fit(x[:left_end], y[:left_end])
            transition = _mini_dma_line_fit(x[left_end:right_start], y[left_end:right_start])
            if before is None or transition is None:
                continue
            if transition.slope >= 0.0:
                continue
            baseline_slope = max(abs(before.slope), abs(after.slope))
            slope_gain = abs(transition.slope) - baseline_slope
            if slope_gain <= 0.0:
                continue
            if baseline_slope > 1e-12 and abs(transition.slope) / baseline_slope < 1.35:
                continue
            after_limit = max(0.01, hint * 2.5, abs(transition.slope) * 0.32)
            if abs(after.slope) > after_limit:
                continue
            start_x = _mini_dma_line_intersection_x(before, transition)
            finish_x = _mini_dma_line_intersection_x(transition, after)
            if start_x is None or finish_x is None:
                continue
            start_x = min(max(start_x, lower), upper)
            finish_x = min(max(finish_x, lower), upper)
            if finish_x < start_x:
                start_x, finish_x = finish_x, start_x
            if finish_x - start_x < scan_width * 0.03:
                continue
            slack = scan_width * 0.08
            if not _mini_dma_near_boundary(start_x, before.end_x, transition.start_x, slack):
                continue
            if not _mini_dma_near_boundary(finish_x, transition.end_x, after.start_x, slack):
                continue
            rmse = _mini_dma_combined_rmse(
                (before.rmse, left_end),
                (transition.rmse, right_start - left_end),
                (after.rmse, n - right_start),
            )
            fit = TangentTransitionFit(
                start_x=float(start_x),
                finish_x=float(finish_x),
                before=before,
                transition=transition,
                after=after,
                rmse=rmse,
            )
            after_penalty = abs(after.slope) / max(after_limit, 1e-12)
            score = (rmse / max(slope_gain, 1e-12)) + (after_penalty * 0.25)
            if best is None or score < best[0]:
                best = (score, fit)
    return best[1] if best is not None else None


def _mini_dma_clean_xy(
    x_values: Iterable[float],
    y_values: Iterable[float],
) -> tuple[np.ndarray, np.ndarray]:
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


def _mini_dma_line_fit(x_values: np.ndarray, y_values: np.ndarray) -> LinearSegmentFit | None:
    mask = np.isfinite(x_values) & np.isfinite(y_values)
    x = x_values[mask]
    y = y_values[mask]
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


def _mini_dma_line_intersection_x(left: LinearSegmentFit, right: LinearSegmentFit) -> float | None:
    denominator = left.slope - right.slope
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        return None
    value = (right.intercept - left.intercept) / denominator
    return float(value) if math.isfinite(float(value)) else None


def _mini_dma_near_boundary(
    intersection_x: float,
    left_end_x: float,
    right_start_x: float,
    slack: float,
) -> bool:
    lower = min(float(left_end_x), float(right_start_x)) - slack
    upper = max(float(left_end_x), float(right_start_x)) + slack
    return lower <= float(intersection_x) <= upper


def _mini_dma_combined_rmse(*segments: tuple[float, int]) -> float:
    total = sum(count for _rmse, count in segments)
    if total <= 0:
        return float("inf")
    return float(math.sqrt(sum((rmse**2) * count for rmse, count in segments) / total))


def _detect_break_point(
    run: MiniDmaRun,
    *,
    voltage_limit_v: float | None = None,
) -> CurrentSweepBreakSummary | None:
    explicit = _explicit_break_point(run)
    if explicit is not None:
        return explicit
    return _voltage_limit_break_point(run, voltage_limit_v=voltage_limit_v)


def _explicit_break_point(run: MiniDmaRun) -> CurrentSweepBreakSummary | None:
    metadata = _metadata_for_run(run.measurement_path)
    for section_name in ("break", "break_point", "wire_break"):
        section = metadata.get(section_name)
        if not isinstance(section, dict):
            continue
        stress = _dict_float(section, "stress_mpa", "target_mpa", "target")
        current = _dict_float(section, "current_mA", "current_ma", "current_set_mA")
        if stress is None:
            continue
        return CurrentSweepBreakSummary(
            stress_mpa=stress,
            load_g=_load_g_from_stress_mpa(run, stress),
            current_mA=current,
            reason="metadata",
        )
    return None


def _voltage_limit_break_point(
    run: MiniDmaRun,
    *,
    voltage_limit_v: float | None = None,
) -> CurrentSweepBreakSummary | None:
    frame = run.frame
    required = {"automation_target_value", "current_set_mA", "current_measured_mA", "voltage_V"}
    if not required.issubset(frame.columns):
        return None
    limit_v = voltage_limit_v
    if limit_v is None:
        limit_v = _metadata_voltage_limit_v(run.measurement_path)
    if limit_v is None or limit_v <= 0.0:
        return None

    stress = pd.to_numeric(frame["automation_target_value"], errors="coerce")
    set_current = pd.to_numeric(frame["current_set_mA"], errors="coerce")
    measured_current = pd.to_numeric(frame["current_measured_mA"], errors="coerce")
    voltage = pd.to_numeric(frame["voltage_V"], errors="coerce")
    mask = (
        stress.notna()
        & set_current.notna()
        & measured_current.notna()
        & voltage.notna()
        & (set_current.abs() >= 5.0)
        & (voltage >= limit_v * 0.95)
        & (measured_current.abs() <= set_current.abs().clip(lower=10.0) * 0.1)
    )
    if not bool(mask.any()):
        return None
    first_index = mask[mask].index[0]
    stress_mpa = float(stress.loc[first_index])
    current_mA = float(set_current.loc[first_index])
    return CurrentSweepBreakSummary(
        stress_mpa=stress_mpa,
        load_g=_load_g_from_stress_mpa(run, stress_mpa),
        current_mA=current_mA,
        reason="voltage_limit_current_collapse",
    )


def _metadata_voltage_limit_v(measurement_path: Path) -> float | None:
    metadata = _metadata_for_run(measurement_path)
    heating = metadata.get("heating")
    if isinstance(heating, dict):
        parsed = _dict_float(heating, "voltage_limit_v", "max_voltage")
        if parsed is not None:
            return parsed
    return _dict_float(metadata, "voltage_limit_v", "max_voltage")


def _dict_float(payload: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _target_token(value: float) -> str:
    label = _format_stress_label(value).replace(" ", "_").replace(".", "p")
    return "".join(char if char.isalnum() or char == "_" else "_" for char in label)


def _normalised_excluded_dir_names(exclude_dir_names: Collection[str] | None) -> set[str]:
    names = MINI_DMA_EXCLUDED_DISCOVERY_DIR_NAMES if exclude_dir_names is None else exclude_dir_names
    return {str(name).strip().casefold() for name in names if str(name).strip()}


def _path_has_excluded_dir(path: Path, excluded_names: Collection[str]) -> bool:
    if not excluded_names:
        return False
    return any(part.casefold() in excluded_names for part in path.parts[:-1])


def iter_measurement_paths(
    paths: Iterable[Path],
    *,
    exclude_dir_names: Collection[str] | None = None,
    require_measurement_data: bool = True,
) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    excluded_names = _normalised_excluded_dir_names(exclude_dir_names)

    def _add(candidate: Path) -> None:
        try:
            measurement = resolve_measurement_path(candidate).resolve()
        except ValueError:
            return
        if _path_has_excluded_dir(measurement, excluded_names):
            return
        if require_measurement_data and not looks_like_measurement_file(measurement):
            return
        if measurement in seen:
            return
        seen.add(measurement)
        resolved.append(measurement)

    for path in paths:
        candidate = Path(path)
        _add(candidate)
        if not candidate.is_dir():
            continue
        try:
            children = sorted(candidate.rglob(MEASUREMENT_FILE), key=lambda item: str(item))
        except OSError:
            continue
        for child in children:
            if _path_has_excluded_dir(child, excluded_names):
                continue
            _add(child)
    return resolved
