from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Iterable

import pandas as pd
from matplotlib.figure import Figure

from plotting.shared.power_axis import add_power_top_axis

MEASUREMENT_FILE = "measurement.csv"
PLOT_PHASES = {"current"}
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
STRAIN_BASELINE_RAW = "raw"
STRAIN_BASELINE_GLOBAL_MINIMUM = "global_minimum"
STRAIN_BASELINE_PER_TARGET_MINIMUM = "per_target_minimum"
STRAIN_BASELINE_MODES = {
    STRAIN_BASELINE_RAW,
    STRAIN_BASELINE_GLOBAL_MINIMUM,
    STRAIN_BASELINE_PER_TARGET_MINIMUM,
}


@dataclass(frozen=True)
class MiniDmaRun:
    path: Path
    measurement_path: Path
    frame: pd.DataFrame
    sample_name: str
    initial_length_mm: float | None = None
    wire_diameter_mm: float | None = None


def resolve_measurement_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / MEASUREMENT_FILE
    if candidate.name.casefold() != MEASUREMENT_FILE.casefold():
        raise ValueError("Select a Mini DMA run folder or measurement.csv file.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"Mini DMA measurement file not found: {candidate}")
    return candidate


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


def current_sweep_groups(frame: pd.DataFrame) -> list[tuple[float, pd.DataFrame]]:
    filtered = frame[frame["automation_phase"].isin(PLOT_PHASES)].copy()
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
        usable = group.sort_values("elapsed_s", kind="stable").copy()
        usable = usable[usable["current_mA"].abs() > 0.0]
        usable = usable.drop_duplicates(
            subset=["current_mA", "strain_pct", "resistance_ohm"],
            keep="first",
        )
        if len(usable) < MIN_POINTS_PER_TARGET:
            continue
        groups.append((target_value, usable.reset_index(drop=True)))
    return groups


def make_strain_current_figure(
    run: MiniDmaRun,
    *,
    zero_minimum_strain: bool = False,
    strain_baseline_mode: str | None = None,
    show_power_top_axis: bool = False,
) -> Figure:
    baseline_mode = _normalise_strain_baseline_mode(
        strain_baseline_mode,
        zero_minimum_strain=zero_minimum_strain,
    )
    return _make_current_figure(
        run,
        y_column="strain_pct",
        y_label=_strain_axis_label(baseline_mode),
        title_suffix="Strain vs Current",
        strain_baseline_mode=baseline_mode,
        show_power_top_axis=show_power_top_axis,
    )


def make_resistance_current_figure(
    run: MiniDmaRun,
    *,
    show_power_top_axis: bool = False,
) -> Figure:
    return _make_current_figure(
        run,
        y_column="resistance_ohm",
        y_label="Resistance [Ohm]",
        title_suffix="Resistance vs Current",
        filter_resistance_outliers=True,
        show_power_top_axis=show_power_top_axis,
    )


def _make_current_figure(
    run: MiniDmaRun,
    *,
    y_column: str,
    y_label: str,
    title_suffix: str,
    filter_resistance_outliers: bool = False,
    strain_baseline_mode: str = STRAIN_BASELINE_RAW,
    show_power_top_axis: bool = False,
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
        ax.plot(
            group["current_mA"].to_numpy(dtype=float),
            y_values,
            label=_format_target_label(run, target),
            linewidth=1.4,
            marker="o",
            markersize=3.5,
        )
    ax.set_title(f"{run.sample_name} - {title_suffix}")
    ax.set_xlabel(_current_axis_label(run, [group for _target, group in plotted_groups]))
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    if show_power_top_axis:
        add_power_top_axis(
            ax,
            power_currents,
            power_resistances,
            label="Power [mW]",
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


def _strain_from_trace_minimum_length(run: MiniDmaRun, group: pd.DataFrame) -> pd.Series:
    """Recalculate strain with each curve's shortest measured length as l0."""
    length_mm = _length_trace_mm(run, group)
    if length_mm is not None and length_mm.notna().any():
        l0_mm = float(length_mm.min(skipna=True))
        if pd.notna(l0_mm) and l0_mm > 0.0:
            return (length_mm - l0_mm) / l0_mm * 100.0

    strain_pct = pd.to_numeric(group["strain_pct"], errors="coerce")
    return strain_pct - strain_pct.min(skipna=True)


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


def _strain_axis_label(mode: str) -> str:
    _ = mode
    return "Strain [%]"


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


def _format_target_label(run: MiniDmaRun, value: float) -> str:
    stress_label = _format_stress_label(value)
    load_g = _load_g_from_stress_mpa(run, value)
    if load_g is None:
        return stress_label
    return f"{stress_label} / {_format_compact_number(load_g)} g"


def _load_g_from_stress_mpa(run: MiniDmaRun, stress_mpa: float) -> float | None:
    area_mm2 = _wire_area_mm2(run)
    if area_mm2 is None:
        return None
    return stress_mpa * area_mm2 / 9.80665 * 1000.0


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
        f"({_format_compact_number(max_current_mA)} mA = "
        f"{_format_compact_number(current_density, max_decimals=0)} A/mm², "
        f"d = {_format_compact_number(diameter_um)} µm)"
    )


def _format_compact_number(value: float, *, max_decimals: int = 2) -> str:
    rounded = round(float(value), max_decimals)
    if math.isclose(rounded, round(rounded), abs_tol=0.5 * (10**-max_decimals)):
        return str(int(round(rounded)))
    return f"{rounded:.{max_decimals}f}".rstrip("0").rstrip(".")


def _target_token(value: float) -> str:
    label = _format_stress_label(value).replace(" ", "_").replace(".", "p")
    return "".join(char if char.isalnum() or char == "_" else "_" for char in label)


def iter_measurement_paths(paths: Iterable[Path]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            measurement = resolve_measurement_path(Path(path)).resolve()
        except ValueError:
            continue
        if measurement in seen:
            continue
        seen.add(measurement)
        resolved.append(measurement)
    return resolved
