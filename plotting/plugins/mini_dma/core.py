from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

import pandas as pd
from matplotlib.figure import Figure

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


@dataclass(frozen=True)
class MiniDmaRun:
    path: Path
    measurement_path: Path
    frame: pd.DataFrame
    sample_name: str


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
    ):
        if column in cleaned.columns:
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned["automation_phase"] = cleaned["automation_phase"].astype(str)
    cleaned = cleaned.dropna(
        subset=["current_mA", "automation_target_value", "strain_pct", "resistance_ohm"]
    )
    if cleaned.empty:
        raise ValueError("No usable Mini DMA current-sweep rows found.")

    sample_name = _sample_name_for_run(measurement_path)
    run_path = measurement_path.parent
    return MiniDmaRun(
        path=run_path,
        measurement_path=measurement_path,
        frame=cleaned.reset_index(drop=True),
        sample_name=sample_name,
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


def make_strain_current_figure(run: MiniDmaRun, *, zero_minimum_strain: bool = False) -> Figure:
    return _make_current_figure(
        run,
        y_column="strain_pct",
        y_label=(
            "Strain relative to trace minimum [%]"
            if zero_minimum_strain
            else "Strain [%]"
        ),
        title_suffix="Strain vs Current",
        zero_minimum_y=zero_minimum_strain,
    )


def make_resistance_current_figure(run: MiniDmaRun) -> Figure:
    return _make_current_figure(
        run,
        y_column="resistance_ohm",
        y_label="Resistance [Ohm]",
        title_suffix="Resistance vs Current",
        filter_resistance_outliers=True,
    )


def _make_current_figure(
    run: MiniDmaRun,
    *,
    y_column: str,
    y_label: str,
    title_suffix: str,
    filter_resistance_outliers: bool = False,
    zero_minimum_y: bool = False,
) -> Figure:
    groups = current_sweep_groups(run.frame)
    if not groups:
        raise ValueError("No current-sweep target groups with enough points.")

    fig = Figure(figsize=(8.0, 5.0), constrained_layout=True)
    ax = fig.add_subplot(111)
    for target, group in groups:
        if filter_resistance_outliers:
            group = _drop_resistance_outliers(group)
        if len(group) < MIN_POINTS_PER_TARGET:
            continue
        y_values = group[y_column].to_numpy(dtype=float)
        if zero_minimum_y and len(y_values):
            y_values = y_values - y_values.min()
        ax.plot(
            group["current_mA"].to_numpy(dtype=float),
            y_values,
            label=_format_target_label(target),
            linewidth=1.4,
        )
    ax.set_title(f"{run.sample_name} - {title_suffix}")
    ax.set_xlabel("Measured current [mA]")
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8, title="Target stress")
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


def _choose_current_column(frame: pd.DataFrame) -> str | None:
    for column in CURRENT_COLUMNS:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if not values.empty and values.abs().sum() > 0.0:
            return column
    return None


def _sample_name_for_run(measurement_path: Path) -> str:
    metadata_path = measurement_path.with_name("metadata.json")
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        name = payload.get("sample_name") if isinstance(payload, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip().replace("/", "_")
    return measurement_path.parent.name


def _format_target_label(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)} MPa"
    return f"{value:g} MPa"


def _target_token(value: float) -> str:
    label = _format_target_label(value).replace(" ", "_").replace(".", "p")
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
