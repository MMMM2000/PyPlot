"""TMA per-run summary image generation.

The generated images are derived caches. Raw CSV/JSON files remain the source of
truth and summaries can be regenerated when plotting improves.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import Colormap, Normalize
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from .run_quality import (
    RunQuality,
    _read_csv_rows_validated,
    analyze_and_write_run_quality,
    analyze_run_quality,
)
from .time_axis import TimeAxisDisplay, time_axis_display

MAX_TEMPERATURE_SIDECAR_BYTES = 512 * 1024 * 1024


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    rows, _row_count, _warnings = _read_csv_rows_validated(path)
    return rows


def _read_csv_frame(path: Path, *, usecols: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(
            path,
            encoding="utf-8-sig",
            usecols=usecols,
            on_bad_lines="skip",
        )
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def _read_control_error_frame(path: Path, *, max_rows: int = 5000) -> pd.DataFrame:
    columns = _csv_header(path)
    wanted = [name for name in ("elapsed_s", "error_value", "tolerance") if name in columns]
    if not {"elapsed_s", "error_value"}.issubset(wanted):
        return pd.DataFrame()
    try:
        size = path.stat().st_size
    except OSError:
        return pd.DataFrame()
    if size <= 32 * 1024 * 1024:
        return _read_csv_frame(path, usecols=wanted)
    return _read_sparse_csv_frame(path, wanted, max_rows=max_rows)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_save_figure(fig: Any, path: Path, *, dpi: int) -> None:
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp{path.suffix}")
    try:
        fig.savefig(temporary, dpi=dpi)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_temperature_sidecar_frame(run_path: Path, *, max_rows: int = 5000) -> pd.DataFrame:
    path = run_path / "ir_temperature.csv"
    if not path.exists() or path.stat().st_size <= 0:
        return pd.DataFrame()
    if path.stat().st_size > MAX_TEMPERATURE_SIDECAR_BYTES:
        return pd.DataFrame()
    columns = _csv_header(path)
    wanted = [
        name
        for name in (
            "elapsed_s",
            "object_c_apparent",
            "frame_max_c",
            "ambient_c",
            "delta_c",
        )
        if name in columns
    ]
    if "elapsed_s" not in wanted or not any(name in wanted for name in ("object_c_apparent", "frame_max_c")):
        return pd.DataFrame()
    if path.stat().st_size <= 128 * 1024 * 1024:
        try:
            return pd.read_csv(path, encoding="utf-8-sig", usecols=wanted)
        except (OSError, ValueError, pd.errors.ParserError):
            return pd.DataFrame()
    return _read_sparse_csv_frame(path, wanted, max_rows=max_rows)


def _frame_has_temperature(df: pd.DataFrame) -> bool:
    for name in ("ir_object_c_apparent", "frame_max_c", "object_c_apparent"):
        if name in df and _series(df, name).notna().any():
            return True
    return False


def _csv_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            return next(reader, [])
    except (OSError, StopIteration, UnicodeDecodeError):
        return []


def _read_sparse_csv_frame(path: Path, columns: list[str], *, max_rows: int) -> pd.DataFrame:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header_bytes = handle.readline()
            header_end = handle.tell()
            header_text = header_bytes.decode("utf-8-sig", errors="replace")
            if size <= header_end:
                return pd.DataFrame()
            offsets = np.linspace(header_end, max(header_end, size - 2), max_rows, dtype=np.int64)
            records: list[dict[str, str]] = []
            seen: set[tuple[str, ...]] = set()
            for offset in offsets:
                handle.seek(int(offset))
                if int(offset) > header_end:
                    handle.readline()
                line = handle.readline()
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace")
                try:
                    row = next(csv.DictReader(io.StringIO(header_text + text)))
                except (csv.Error, StopIteration):
                    continue
                record = {column: row.get(column, "") for column in columns}
                key = tuple(record.get(column, "") for column in columns)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
    except OSError:
        return pd.DataFrame()
    frame = pd.DataFrame.from_records(records, columns=columns)
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "elapsed_s" in frame:
        frame = frame.dropna(subset=["elapsed_s"]).sort_values("elapsed_s", kind="stable")
    return frame.reset_index(drop=True)


def _series(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[name], errors="coerce")


def _clean_xy(df: pd.DataFrame, x_name: str, y_name: str) -> tuple[np.ndarray, np.ndarray]:
    x = _series(df, x_name)
    y = _series(df, y_name)
    mask = x.notna() & y.notna()
    return x[mask].to_numpy(), y[mask].to_numpy()


def _decimate(x: np.ndarray, y: np.ndarray, limit: int = 4500) -> tuple[np.ndarray, np.ndarray]:
    if len(x) <= limit:
        return x, y
    index = np.linspace(0, len(x) - 1, limit).astype(int)
    return x[index], y[index]


def _metadata_float(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    number = _float_or_none(value)
    return number if number is not None and number > 0 else None


def _format_compact_number(value: float, *, max_decimals: int = 2) -> str:
    rounded = round(float(value), max_decimals)
    if math.isclose(rounded, round(rounded), abs_tol=0.5 * (10**-max_decimals)):
        return f"{int(round(rounded))}"
    text = f"{rounded:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_metric(value: float | int | None, unit: str = "", digits: int = 1) -> str:
    number = _float_or_none(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}{unit}"


def _format_power_tick(value_mw: float) -> str:
    if not math.isfinite(value_mw):
        return ""
    if abs(value_mw) >= 100:
        return f"{value_mw:.0f}"
    if abs(value_mw) >= 10:
        return f"{value_mw:.1f}".rstrip("0").rstrip(".")
    if abs(value_mw) >= 1:
        return f"{value_mw:.2f}".rstrip("0").rstrip(".")
    return f"{value_mw:.3f}".rstrip("0").rstrip(".")


def _format_stress_load_label(metadata: dict[str, Any], stress_mpa: float) -> str:
    stress_label = f"{int(stress_mpa)} MPa" if float(stress_mpa).is_integer() else f"{stress_mpa:g} MPa"
    diameter_mm = _metadata_float(metadata, "wire_diameter_mm")
    if diameter_mm is None:
        return stress_label
    area_mm2 = math.pi * diameter_mm * diameter_mm / 4.0
    load_g = float(stress_mpa) * area_mm2 / 9.80665 * 1000.0
    return f"{stress_label} / {_format_compact_number(load_g)} g"


def _max_or_none(values: pd.Series) -> float | None:
    values = values.dropna()
    if values.empty:
        return None
    return float(values.max())


def _rolling_median(y: pd.Series, window: int) -> pd.Series:
    if y.empty:
        return y
    window = max(3, min(window, max(3, len(y) // 12)))
    if window % 2 == 0:
        window += 1
    return y.rolling(window=window, center=True, min_periods=1).median()


def _power_tick_positions_and_labels(
    current_ma: pd.Series,
    resistance_ohm: pd.Series,
    *,
    tick_positions: np.ndarray,
) -> tuple[np.ndarray, list[str]] | None:
    current = pd.to_numeric(current_ma, errors="coerce")
    resistance = pd.to_numeric(resistance_ohm, errors="coerce")
    frame = pd.DataFrame({"current_mA": current, "resistance_ohm": resistance}).replace(
        [np.inf, -np.inf], np.nan
    )
    frame = frame.dropna()
    if frame.empty:
        return None
    frame["power_mW"] = frame["current_mA"] * frame["current_mA"] * frame["resistance_ohm"] / 1000.0
    grouped = (
        frame.groupby("current_mA", sort=True, as_index=False)["power_mW"]
        .median()
        .sort_values("current_mA", kind="stable")
    )
    if grouped["current_mA"].nunique() < 2:
        return None
    x_values = grouped["current_mA"].to_numpy(dtype=float)
    p_values = grouped["power_mW"].to_numpy(dtype=float)
    xmin = float(np.nanmin(x_values))
    xmax = float(np.nanmax(x_values))
    ticks: list[float] = []
    labels: list[str] = []
    for tick in tick_positions:
        if not math.isfinite(float(tick)) or tick < xmin or tick > xmax:
            continue
        ticks.append(float(tick))
        labels.append(_format_power_tick(float(np.interp(tick, x_values, p_values))))
    if len(ticks) < 2:
        return None
    return np.asarray(ticks, dtype=float), labels


def _plot_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows for result plots, excluding obvious electrical fault tail samples."""

    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    current_set = _series(df, "current_set_mA")
    current_measured = _series(df, "current_measured_mA")
    voltage = _series(df, "voltage_V")
    resistance = _series(df, "resistance_ohm")
    compliance = (
        current_set.notna()
        & current_measured.notna()
        & voltage.notna()
        & (current_set.abs() >= 2.0)
        & (current_measured.abs() < current_set.abs() * 0.25)
        & (voltage >= 25.0)
    )
    mask &= ~compliance

    finite_resistance = resistance.replace([np.inf, -np.inf], np.nan).dropna()
    if len(finite_resistance) >= 20:
        median = float(finite_resistance.median())
        q95 = float(finite_resistance.quantile(0.95))
        robust_limit = max(q95 * 1.75, median * 3.0, median + 100.0)
        mask &= resistance.isna() | (resistance <= robust_limit)
    return df[mask].copy()


def _stop_color(metadata: dict[str, Any], quality: RunQuality) -> str:
    stop = metadata.get("stop") if isinstance(metadata.get("stop"), dict) else {}
    category = str(stop.get("category") or "")
    reason = str(stop.get("reason") or quality.stop_reason or "")
    if category == "normal" or reason in {"recipe_completed", "completed"}:
        return "#16a34a"
    if category == "fault" or "break" in reason or "fault" in reason:
        return "#dc2626"
    return "#d97706"


def _add_banner(fig: plt.Figure, run_dir: Path, metadata: dict[str, Any], quality: RunQuality, df: pd.DataFrame) -> None:
    stop = metadata.get("stop") if isinstance(metadata.get("stop"), dict) else {}
    elapsed = _max_or_none(_series(df, "elapsed_s"))
    max_current = _max_or_none(_series(df, "current_measured_mA"))
    max_stress = _max_or_none(_series(df, "stress_mpa"))
    max_strain = _max_or_none(_series(df, "strain_pct"))
    max_temp = _max_or_none(_series(df, "ir_object_c_apparent"))
    if max_temp is None:
        max_temp = _max_or_none(_series(df, "frame_max_c"))
    diameter_mm = _metadata_float(metadata, "wire_diameter_mm")
    length_mm = _metadata_float(metadata, "initial_length_mm")
    sample_bits = []
    if diameter_mm:
        sample_bits.append(f"d {_format_compact_number(diameter_mm * 1000.0)} um")
    if length_mm:
        sample_bits.append(f"l0 {_format_compact_number(length_mm, max_decimals=3)} mm")
    fig.text(0.015, 0.972, run_dir.name, fontsize=16, fontweight="bold", va="top", ha="left")
    fig.text(
        0.015,
        0.935,
        f"{stop.get('label') or stop.get('reason') or quality.stop_reason or 'status n/a'}",
        fontsize=10,
        color="white",
        va="top",
        ha="left",
        bbox={"boxstyle": "round,pad=0.28", "facecolor": _stop_color(metadata, quality), "edgecolor": "none"},
    )
    fig.text(
        0.26,
        0.945,
        " | ".join(
            sample_bits
            + [
                f"duration {_format_metric(elapsed, ' s', 0)}",
                f"points {len(df)}",
                f"max stress {_format_metric(max_stress, ' MPa')}",
                f"max strain {_format_metric(max_strain, '%')}",
                f"max current {_format_metric(max_current, ' mA')}",
                f"max temp {_format_metric(max_temp, ' C')}",
            ]
        ),
        fontsize=10,
        va="top",
        ha="left",
    )
    detail = str(stop.get("detail") or "")
    if detail:
        fig.text(0.015, 0.022, detail[:230], fontsize=8.5, color="#374151", ha="left", va="bottom")


def _shade_holds(
    ax: Axes,
    df: pd.DataFrame,
    time_axis: TimeAxisDisplay,
) -> None:
    if "automation_phase" not in df or "elapsed_s" not in df:
        return
    elapsed = _series(df, "elapsed_s")
    is_hold = df["automation_phase"].astype(str).eq("current_hold")
    start = None
    previous = None
    labelled = False
    for t_value, is_active in zip(elapsed, is_hold):
        if pd.isna(t_value):
            continue
        if is_active:
            if start is None:
                start = float(t_value)
            previous = float(t_value)
        elif start is not None:
            ax.axvspan(
                start / time_axis.divisor_s,
                (previous if previous is not None else start) / time_axis.divisor_s,
                color="#f59e0b",
                alpha=0.08,
                lw=0,
                label=None if labelled else "current hold",
            )
            labelled = True
            start = None
            previous = None
    if start is not None:
        ax.axvspan(
            start / time_axis.divisor_s,
            (previous if previous is not None else start) / time_axis.divisor_s,
            color="#f59e0b",
            alpha=0.08,
            lw=0,
            label=None if labelled else "current hold",
        )


def _style_axis(ax: Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, color="#d1d5db", alpha=0.55, linewidth=0.7)
    ax.tick_params(labelsize=9)


def _add_power_per_cm_axis(ax: Axes, df: pd.DataFrame, metadata: dict[str, Any]) -> None:
    if "resistance_ohm" not in df:
        return
    current_name = "current_measured_mA" if "current_measured_mA" in df else "current_set_mA"
    current = _series(df, current_name)
    resistance = _series(df, "resistance_ohm")
    length_mm = _metadata_float(metadata, "initial_length_mm")
    label = "Power [mW]"
    if length_mm:
        resistance = resistance / (length_mm / 10.0)
        label = "Power [mW/cm]"
    ticks_and_labels = _power_tick_positions_and_labels(
        current,
        resistance,
        tick_positions=np.asarray(ax.get_xticks(), dtype=float),
    )
    if ticks_and_labels is None:
        return
    ticks, labels = ticks_and_labels
    top_ax = ax.twiny()
    top_ax.set_xticks(ticks)
    top_ax.set_xticklabels(labels)
    top_ax.set_xlim(ax.get_xlim())
    top_ax.set_xlabel(label)
    top_ax.tick_params(axis="x", labelsize=8)


def _set_current_axis_limits(ax: Axes, df: pd.DataFrame, current_name: str) -> None:
    current = _series(df, current_name).replace([np.inf, -np.inf], np.nan).dropna()
    if current.empty:
        return
    minimum = float(current.min())
    maximum = float(current.max())
    span = maximum - minimum
    padding = max(0.25, span * 0.025)
    ax.set_xlim(minimum - padding, maximum + padding)


def _plot_stress_time(
    ax: Axes,
    df: pd.DataFrame,
    time_axis: TimeAxisDisplay,
    metadata: dict[str, Any] | None = None,
) -> None:
    x, y = _clean_xy(df, "elapsed_s", "stress_mpa")
    x, y = _decimate(x, y)
    ax.plot(x / time_axis.divisor_s, y, color="#2563eb", lw=1.25, label="stress")
    _shade_holds(ax, df, time_axis)
    mode = ""
    if "recipe_mode" in df and not df["recipe_mode"].dropna().empty:
        mode = str(df["recipe_mode"].dropna().mode().iloc[0])
    if mode in {"current_sweep_stress", "stress_ramp"}:
        phase = df.get("automation_basis", pd.Series("", index=df.index)).astype(str)
        target = _series(df, "automation_target_value")
        elapsed = _series(df, "elapsed_s")
        mask = phase.eq("stress_mpa") & elapsed.notna() & target.notna()
        tx, ty = elapsed[mask].to_numpy(), target[mask].to_numpy()
        tx, ty = _decimate(tx, ty)
        if len(tx):
            ax.plot(
                tx / time_axis.divisor_s,
                ty,
                color="#111827",
                lw=1.0,
                ls="--",
                alpha=0.7,
                label="target",
            )
    _style_axis(ax, "Stress vs time", time_axis.label, "Stress (MPa)")
    diameter_mm = _metadata_float(metadata or {}, "wire_diameter_mm")
    if diameter_mm is not None:
        stress_to_load = math.pi * diameter_mm * diameter_mm / 4.0 / 9.80665 * 1000.0
        load_axis = ax.secondary_yaxis(
            "right",
            functions=(
                lambda stress: stress * stress_to_load,
                lambda load: load / stress_to_load,
            ),
        )
        load_axis.set_ylabel("Load (g)", color="#d97706", labelpad=3)
        load_axis.tick_params(axis="y", labelsize=9, labelcolor="#d97706")
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, loc="best")


def _plateau_legend_label(part: pd.DataFrame, fallback: str, metadata: dict[str, Any]) -> str:
    if "automation_basis" in part and "automation_target_value" in part:
        basis = part["automation_basis"].astype(str)
        target = _series(part, "automation_target_value")
        stress_targets = target[basis.eq("stress_mpa") & target.notna()]
        if not stress_targets.empty:
            return _format_stress_load_label(metadata, float(stress_targets.median()))
    if "plateau_label" in part:
        labels = part["plateau_label"].dropna().astype(str)
        labels = labels[labels.str.len() > 0]
        if not labels.empty:
            text = labels.mode().iloc[0]
            for token in text.replace("(", " ").replace(")", " ").split():
                value = _float_or_none(token)
                if value is not None and "MPa" in text:
                    return _format_stress_load_label(metadata, value)
            return text
    target = _series(part, "automation_target_value").dropna()
    if not target.empty:
        return _format_stress_load_label(metadata, float(target.median()))
    return fallback


@dataclass
class _PlateauGroup:
    label: str
    target_stress_mpa: float
    rows: pd.DataFrame


@dataclass
class _PlateauPlotContext:
    groups: list[_PlateauGroup]
    norm: Normalize
    cmap: Colormap


def _plateau_target_stress(part: pd.DataFrame) -> float | None:
    if "automation_basis" in part and "automation_target_value" in part:
        basis = part["automation_basis"].astype(str)
        target = _series(part, "automation_target_value")
        values = target[basis.eq("stress_mpa") & target.notna()]
        if not values.empty:
            return float(values.median())
    target = _series(part, "automation_target_value").dropna()
    if not target.empty:
        return float(target.median())
    stress = _series(part, "stress_mpa").dropna()
    return float(stress.median()) if not stress.empty else None


def _plateau_plot_context(
    df: pd.DataFrame,
    metadata: dict[str, Any],
) -> _PlateauPlotContext | None:
    df = _plot_rows(df)
    if df.empty:
        return None
    plateau = _series(df, "plateau_index")
    grouped_parts: list[tuple[str, pd.DataFrame]] = []
    if not plateau.empty and plateau.notna().any():
        # Normal stress-ladder rows have numbered plateaus. First overheating is
        # deliberately unindexed, so this keeps conditioning out of comparison
        # panels without truncating the first normal 1 mA stress ramp.
        normal = df.loc[plateau.notna()].copy()
        normal["_plot_plateau_index"] = plateau.loc[plateau.notna()].to_numpy()
        grouped_parts = [
            (str(label), part.drop(columns="_plot_plateau_index"))
            for label, part in normal.groupby("_plot_plateau_index", sort=True)
        ]
    else:
        # Older result files can predate numeric plateau indices. Preserve a
        # useful fallback rather than returning an empty plot.
        keys = pd.Series("unindexed", index=df.index, dtype=object)
        if "automation_basis" in df and "automation_target_value" in df:
            basis = df["automation_basis"].astype(str)
            target = _series(df, "automation_target_value")
            valid = target.notna() & basis.str.len().gt(0)
            keys.loc[valid] = [
                f"{basis_value}:{target_value:.9g}"
                for basis_value, target_value in zip(basis.loc[valid], target.loc[valid])
            ]
        grouped_parts = [(str(label), part) for label, part in df.groupby(keys, sort=True)]

    groups: list[_PlateauGroup] = []
    for fallback, part in grouped_parts:
        target = _plateau_target_stress(part)
        if target is None:
            continue
        groups.append(
            _PlateauGroup(
                label=_plateau_legend_label(part, fallback, metadata),
                target_stress_mpa=target,
                rows=part,
            )
        )
    if not groups:
        return None
    targets = np.asarray([group.target_stress_mpa for group in groups], dtype=float)
    minimum = float(np.nanmin(targets))
    maximum = float(np.nanmax(targets))
    if math.isclose(minimum, maximum):
        padding = max(1.0, abs(minimum) * 0.02)
        minimum -= padding
        maximum += padding
    return _PlateauPlotContext(groups, Normalize(minimum, maximum), plt.get_cmap("viridis"))


def _current_direction_parts(part: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    if part.empty:
        return []
    ordered = part.sort_values("elapsed_s", kind="stable") if "elapsed_s" in part else part.copy()
    control_name = "current_set_mA" if "current_set_mA" in ordered else "current_measured_mA"
    current = _series(ordered, control_name)
    if current.empty or not current.notna().any():
        return [("increasing", ordered)]
    maximum = float(current.max())
    peak = np.isclose(current.to_numpy(dtype=float), maximum, atol=max(0.05, abs(maximum) * 0.002))
    peak_positions = np.flatnonzero(peak)
    if not len(peak_positions):
        return [("increasing", ordered)]
    split = int(peak_positions[-1]) + 1
    parts = [("increasing", ordered.iloc[:split])]
    if split < len(ordered):
        parts.append(("decreasing", ordered.iloc[split:]))
    return [(direction, rows) for direction, rows in parts if len(rows) >= 2]


_DIRECTION_STYLE = {
    "increasing": {"linestyle": "-", "marker": "o", "label": "current increasing"},
    "decreasing": {"linestyle": "--", "marker": "x", "label": "current decreasing"},
}


def _plot_grouped_current_response(
    ax: Axes,
    context: _PlateauPlotContext,
    *,
    x_name: str,
    y_name: str,
) -> None:
    for group in context.groups:
        color = context.cmap(context.norm(group.target_stress_mpa))
        for direction, part in _current_direction_parts(group.rows):
            x, y = _clean_xy(part, x_name, y_name)
            if len(x) < 2:
                continue
            x, y = _decimate(x, y, 1400)
            style = _DIRECTION_STYLE[direction]
            ax.plot(
                x,
                y,
                color=color,
                lw=1.05,
                linestyle=style["linestyle"],
                marker=style["marker"],
                markersize=2.5 if direction == "increasing" else 2.9,
                markevery=max(1, len(x) // 14),
                markeredgewidth=0.65,
            )


def _direction_legend_handles() -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            color="#334155",
            linewidth=1.1,
            linestyle=style["linestyle"],
            marker=style["marker"],
            markersize=4.0,
            label=style["label"],
        )
        for style in _DIRECTION_STYLE.values()
    ]


def _add_plateau_colorbar(
    fig: plt.Figure,
    axes: Axes | list[Axes],
    context: _PlateauPlotContext,
) -> None:
    mappable = plt.cm.ScalarMappable(norm=context.norm, cmap=context.cmap)
    colorbar = fig.colorbar(mappable, ax=axes, fraction=0.035, pad=0.025, aspect=32)
    targets = sorted({group.target_stress_mpa for group in context.groups})
    if len(targets) > 6:
        indices = np.linspace(0, len(targets) - 1, 5).round().astype(int)
        targets = [targets[index] for index in sorted(set(indices))]
    colorbar.set_ticks(targets)
    colorbar.set_label("Target stress (MPa)", fontsize=8.5)
    colorbar.ax.tick_params(labelsize=8)


def _plateau_context_frame(context: _PlateauPlotContext) -> pd.DataFrame:
    return pd.concat([group.rows for group in context.groups], ignore_index=True)


def _plot_strain_current(
    ax: Axes,
    df: pd.DataFrame,
    metadata: dict[str, Any],
    *,
    grouped: bool,
    context: _PlateauPlotContext | None = None,
) -> _PlateauPlotContext | None:
    df = _plot_rows(df)
    current_name = "current_measured_mA" if "current_measured_mA" in df else "current_set_mA"
    if grouped:
        context = context or _plateau_plot_context(df, metadata)
        if context is not None:
            _plot_grouped_current_response(
                ax,
                context,
                x_name=current_name,
                y_name="strain_pct",
            )
    else:
        x, y = _clean_xy(df, current_name, "strain_pct")
        x, y = _decimate(x, y)
        ax.plot(x, y, color="#047857", lw=1.35)
    _style_axis(ax, "Strain vs current", "Measured current (mA)", "Strain (%)")
    axis_frame = _plateau_context_frame(context) if context is not None else df
    _set_current_axis_limits(ax, axis_frame, current_name)
    _add_power_per_cm_axis(ax, axis_frame, metadata)
    return context


def _plot_current_resistance(
    ax: Axes,
    df: pd.DataFrame,
    time_axis: TimeAxisDisplay,
) -> None:
    df = _plot_rows(df)
    x, current = _clean_xy(df, "elapsed_s", "current_measured_mA")
    x, current = _decimate(x, current)
    current_color = "#e11d48"
    resistance_color = "#0d9488"
    ax.plot(
        x / time_axis.divisor_s,
        current,
        color=current_color,
        lw=1.2,
        label="current",
    )
    _style_axis(
        ax,
        "Current + resistance vs time",
        time_axis.label,
        "Current (mA)",
    )
    ax.yaxis.label.set_color(current_color)
    ax.tick_params(axis="y", labelcolor=current_color)
    ax2 = ax.twinx()
    rx, resistance = _clean_xy(df, "elapsed_s", "resistance_ohm")
    rx, resistance = _decimate(rx, resistance)
    ax2.plot(
        rx / time_axis.divisor_s,
        resistance,
        color=resistance_color,
        lw=1.0,
        alpha=0.9,
    )
    ax2.set_ylabel("Resistance (ohm)", labelpad=3)
    ax2.yaxis.label.set_color(resistance_color)
    ax2.tick_params(axis="y", labelsize=9, labelcolor=resistance_color)


def _plot_resistance_current(
    ax: Axes,
    df: pd.DataFrame,
    metadata: dict[str, Any],
    *,
    context: _PlateauPlotContext | None = None,
) -> _PlateauPlotContext | None:
    df = _plot_rows(df)
    current_name = "current_measured_mA" if "current_measured_mA" in df else "current_set_mA"
    context = context or _plateau_plot_context(df, metadata)
    if context is not None:
        _plot_grouped_current_response(
            ax,
            context,
            x_name=current_name,
            y_name="resistance_ohm",
        )
    else:
        x, y = _clean_xy(df, current_name, "resistance_ohm")
        x, y = _decimate(x, y)
        ax.plot(x, y, color="#0d9488", lw=1.15)
    _style_axis(ax, "Resistance vs current", "Measured current (mA)", "Resistance (ohm)")
    axis_frame = _plateau_context_frame(context) if context is not None else df
    _set_current_axis_limits(ax, axis_frame, current_name)
    _add_power_per_cm_axis(ax, axis_frame, metadata)
    return context


def _plot_temperature(
    ax: Axes,
    df: pd.DataFrame,
    time_axis: TimeAxisDisplay,
    sidecar_df: pd.DataFrame | None = None,
) -> bool:
    source = df
    temp_name = "ir_object_c_apparent" if "ir_object_c_apparent" in source else "frame_max_c"
    if temp_name not in source or not (_series(source, temp_name).notna().any()):
        source = sidecar_df if sidecar_df is not None else pd.DataFrame()
        temp_name = "object_c_apparent" if "object_c_apparent" in source else "frame_max_c"
    if source.empty or temp_name not in source:
        return False
    elapsed = _series(source, "elapsed_s")
    temp = _series(source, temp_name)
    mask = elapsed.notna() & temp.notna()
    if not mask.any():
        return False
    smooth = _rolling_median(temp[mask], 121)
    x = elapsed[mask].to_numpy()
    y = temp[mask].to_numpy()
    xs, ys = _decimate(x, y)
    ax.plot(
        xs / time_axis.divisor_s,
        ys,
        color="#94a3b8",
        lw=0.8,
        alpha=0.34,
        label="raw",
    )
    xs, ys = _decimate(x, smooth.to_numpy())
    ax.plot(
        xs / time_axis.divisor_s,
        ys,
        color="#dc2626",
        lw=1.8,
        label="processed",
    )
    _style_axis(ax, "Temperature max vs time", time_axis.label, "Temperature (C)")
    ax.legend(fontsize=8, loc="best")
    return True


def _selected_current_levels(context: _PlateauPlotContext) -> list[float]:
    maxima = [
        float(_series(group.rows, "current_set_mA").max())
        for group in context.groups
        if _series(group.rows, "current_set_mA").notna().any()
    ]
    if not maxima:
        return [1.0]
    maximum = max(maxima)
    return [1.0, *[float(value) for value in range(10, int(maximum // 10) * 10 + 1, 10)]]


def _plot_strain_stress(
    ax: Axes,
    df: pd.DataFrame,
    metadata: dict[str, Any] | None = None,
) -> None:
    df = _plot_rows(df)
    mode = ""
    if "recipe_mode" in df and not df["recipe_mode"].dropna().empty:
        mode = str(df["recipe_mode"].dropna().mode().iloc[0])
    if mode and mode != "current_sweep_stress":
        x, y = _clean_xy(df, "strain_pct", "stress_mpa")
        x, y = _decimate(x, y)
        ax.plot(x, y, color="#7c3aed", lw=1.2)
        _style_axis(ax, "Stress vs strain", "Strain (%)", "Stress (MPa)")
        return
    context = _plateau_plot_context(df, metadata or {})
    if context is None:
        x, y = _clean_xy(df, "strain_pct", "stress_mpa")
        x, y = _decimate(x, y)
        ax.plot(x, y, color="#7c3aed", lw=1.2)
        _style_axis(ax, "Stress vs strain", "Strain (%)", "Stress (MPa)")
        return

    levels = _selected_current_levels(context)
    colors = {
        current: plt.get_cmap("plasma")(index / max(1, len(levels) - 1))
        for index, current in enumerate(levels)
    }
    current_window = 0.35
    plotted_levels: set[float] = set()

    # Retain the dense stress-ramp evidence at 1 mA, but keep the reader-facing
    # legend focused on current rather than acquisition details or point counts.
    ramp_parts: list[pd.DataFrame] = []
    for group in context.groups:
        phase = group.rows.get("automation_phase", pd.Series("", index=group.rows.index)).astype(str)
        current = _series(group.rows, "current_set_mA")
        mask = phase.eq("target_ramp") & current.sub(1.0).abs().le(current_window)
        if mask.any():
            ramp_parts.append(group.rows.loc[mask])
    if ramp_parts:
        ramp = pd.concat(ramp_parts).sort_values("elapsed_s", kind="stable")
        x, y = _clean_xy(ramp, "strain_pct", "stress_mpa")
        if len(x):
            x, y = _decimate(x, y, 2200)
            ax.plot(
                x,
                y,
                color=colors[1.0],
                linewidth=0.8,
                marker=".",
                markersize=2.2,
                alpha=0.8,
                zorder=2,
            )
            plotted_levels.add(1.0)

    for current_level in levels:
        color = colors[current_level]
        for direction in _DIRECTION_STYLE:
            records: list[tuple[float, float, float]] = []
            for group in context.groups:
                part_for_direction = dict(_current_direction_parts(group.rows)).get(direction)
                if part_for_direction is None:
                    continue
                current = _series(part_for_direction, "current_set_mA")
                stress = _series(part_for_direction, "stress_mpa")
                strain = _series(part_for_direction, "strain_pct")
                mask = current.sub(current_level).abs().le(current_window) & stress.notna() & strain.notna()
                if not mask.any():
                    continue
                records.append(
                    (
                        group.target_stress_mpa,
                        float(strain.loc[mask].median()),
                        float(stress.loc[mask].median()),
                    )
                )
            if not records:
                continue
            if current_level == 1.0 and direction == "increasing" and ramp_parts:
                continue
            records.sort(key=lambda row: row[0])
            style = _DIRECTION_STYLE[direction]
            ax.plot(
                [row[1] for row in records],
                [row[2] for row in records],
                color=color,
                linestyle=style["linestyle"],
                linewidth=1.15,
                marker=style["marker"],
                markersize=3.0 if direction == "increasing" else 3.4,
                markeredgewidth=0.7,
                zorder=3,
            )
            plotted_levels.add(current_level)

    _style_axis(ax, "Stress vs strain at selected current", "Strain (%)", "Stress (MPa)")
    current_handles = [
        Line2D([0], [0], color=colors[current], linewidth=2.0, label=f"{current:g} mA")
        for current in levels
        if current in plotted_levels
    ]
    if current_handles:
        ax.legend(
            handles=[*current_handles, *_direction_legend_handles()],
            fontsize=6.4,
            ncol=2,
            loc="best",
            framealpha=0.88,
            borderpad=0.35,
            labelspacing=0.25,
            handlelength=1.8,
            columnspacing=0.8,
        )


def _plot_error_trace(
    ax: Axes,
    df: pd.DataFrame,
    trace: pd.DataFrame,
    time_axis: TimeAxisDisplay,
) -> None:
    source = trace if not trace.empty and {"elapsed_s", "error_value"}.issubset(trace.columns) else df
    y_name = "error_value" if "error_value" in source else "stress_mpa"
    x, y = _clean_xy(source, "elapsed_s", y_name)
    x, y = _decimate(x, y)
    ax.axhline(0, color="#111827", lw=0.8, alpha=0.6)
    if y_name == "error_value" and "tolerance" in source:
        elapsed = _series(source, "elapsed_s")
        tolerance = _series(source, "tolerance").abs()
        mask = elapsed.notna() & tolerance.notna()
        if mask.any():
            tx = elapsed.loc[mask].to_numpy() / time_axis.divisor_s
            ty = tolerance.loc[mask].to_numpy()
            tx, ty = _decimate(tx, ty)
            ax.fill_between(tx, -ty, ty, color="#94a3b8", alpha=0.18, linewidth=0, label="tolerance")
    ax.plot(x / time_axis.divisor_s, y, color="#db2777", lw=0.9)
    _style_axis(
        ax,
        "Control error vs time",
        time_axis.label,
        "Stress error (MPa)" if y_name == "error_value" else "Stress (MPa)",
    )
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7.5, loc="best")


def _plot_phone_summary(
    run_dir: Path,
    df: pd.DataFrame,
    trace: pd.DataFrame,
    temperature: pd.DataFrame,
    metadata: dict[str, Any],
    quality: RunQuality,
    out: Path,
) -> None:
    time_axis = time_axis_display(_max_or_none(_series(df, "elapsed_s")) or 0.0)
    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor("white")
    grid = fig.add_gridspec(2, 3, left=0.055, right=0.95, top=0.78, bottom=0.10, wspace=0.32, hspace=0.42)
    _add_banner(fig, run_dir, metadata, quality, df)
    ax_main = fig.add_subplot(grid[:, :2])
    mode = str(df["recipe_mode"].dropna().mode().iloc[0]) if "recipe_mode" in df and not df["recipe_mode"].dropna().empty else ""
    if "constant_current" in mode:
        _plot_strain_stress(ax_main, df, metadata)
        ax_main.set_title("Main result: stress-strain loop", fontsize=13, fontweight="bold")
    else:
        context = _plot_strain_current(ax_main, df, metadata, grouped=True)
        ax_main.set_title("")
        ax_main.set_title("Main result: strain-current curves", fontsize=13, fontweight="bold", loc="left")
        if context is not None:
            fig.legend(
                handles=_direction_legend_handles(),
                fontsize=7.5,
                loc="center right",
                bbox_to_anchor=(0.64, 0.825),
                ncol=2,
                frameon=False,
            )
            _add_plateau_colorbar(fig, ax_main, context)
    _plot_stress_time(fig.add_subplot(grid[0, 2]), df, time_axis, metadata)
    lower_right = fig.add_subplot(grid[1, 2])
    if not _plot_temperature(lower_right, df, time_axis, temperature):
        _plot_current_resistance(lower_right, df, time_axis)
    try:
        _atomic_save_figure(fig, out, dpi=160)
    finally:
        plt.close(fig)


def _plot_detail_summary(
    run_dir: Path,
    df: pd.DataFrame,
    trace: pd.DataFrame,
    temperature: pd.DataFrame,
    metadata: dict[str, Any],
    quality: RunQuality,
    out: Path,
) -> None:
    time_axis = time_axis_display(_max_or_none(_series(df, "elapsed_s")) or 0.0)
    fig, axes = plt.subplots(3, 2, figsize=(15, 13))
    fig.subplots_adjust(left=0.07, right=0.96, top=0.82, bottom=0.07, hspace=0.48, wspace=0.30)
    fig.patch.set_facecolor("white")
    _add_banner(fig, run_dir, metadata, quality, df)
    _plot_stress_time(axes[0, 0], df, time_axis, metadata)
    context = _plateau_plot_context(df, metadata)
    _plot_strain_current(axes[0, 1], df, metadata, grouped=True, context=context)
    _plot_current_resistance(axes[1, 0], df, time_axis)
    resistance_shown = not _plot_temperature(axes[1, 1], df, time_axis, temperature)
    if resistance_shown:
        _plot_resistance_current(axes[1, 1], df, metadata, context=context)
    _plot_error_trace(axes[2, 0], df, trace, time_axis)
    _plot_strain_stress(axes[2, 1], df, metadata)
    if context is not None:
        comparison_axes = [axes[0, 1], axes[1, 1]] if resistance_shown else axes[0, 1]
        _add_plateau_colorbar(fig, comparison_axes, context)
        fig.legend(
            handles=_direction_legend_handles(),
            fontsize=7.5,
            loc="upper right",
            bbox_to_anchor=(0.90, 0.915),
            ncol=2,
            framealpha=0.88,
        )
    try:
        _atomic_save_figure(fig, out, dpi=150)
    finally:
        plt.close(fig)


def _hold_spans(rows: list[dict[str, str]]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    start: float | None = None
    previous: float | None = None
    for row in rows:
        elapsed = _float_or_none(row.get("elapsed_s"))
        if elapsed is None:
            continue
        if str(row.get("automation_phase") or "") == "current_hold":
            if start is None:
                start = elapsed
            previous = elapsed
            continue
        if start is not None:
            spans.append((start, previous if previous is not None else start))
            start = None
            previous = None
    if start is not None:
        spans.append((start, previous if previous is not None else start))
    return spans


def generate_core_run_plot(
    run_dir: Path | str,
    *,
    image_path: Path | str | None = None,
    summary_path: Path | str | None = None,
    detail_image_path: Path | str | None = None,
    write_quality: bool = True,
) -> dict[str, Any]:
    """Generate phone and detail TMA run-summary images."""

    run_path = Path(run_dir)
    missing = [name for name in ("measurement.csv",) if not (run_path / name).exists()]
    if missing:
        raise FileNotFoundError(f"TMA run folder is missing required file(s): {', '.join(missing)} in {run_path}")
    rows = _read_csv_rows(run_path / "measurement.csv")
    df = _read_csv_frame(run_path / "measurement.csv")
    trace = _read_control_error_frame(run_path / "control_trace.csv")
    temperature = pd.DataFrame() if _frame_has_temperature(df) else _read_temperature_sidecar_frame(run_path)
    metadata = _read_json(run_path / "metadata.json")
    quality = analyze_and_write_run_quality(run_path) if write_quality else analyze_run_quality(run_path)
    if image_path is None:
        phone_image = run_path / "run_summary.png"
    else:
        phone_image = Path(image_path)
    if detail_image_path is None:
        detail_image = run_path / "run_summary_detail.png"
    else:
        detail_image = Path(detail_image_path)
    if summary_path is None:
        summary = run_path / "run_summary.json"
    else:
        summary = Path(summary_path)
    phone_image.parent.mkdir(parents=True, exist_ok=True)
    detail_image.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)

    _plot_phone_summary(run_path, df, trace, temperature, metadata, quality, phone_image)
    _plot_detail_summary(run_path, df, trace, temperature, metadata, quality, detail_image)

    hold_time_spans = [
        (float(window["start_s"]), float(window["end_s"]))
        for window in quality.current_hold_windows
        if window.get("start_s") is not None and window.get("end_s") is not None
    ]
    if not hold_time_spans:
        hold_time_spans = _hold_spans(rows)
    payload = {
        "run_dir": str(run_path),
        "image_path": str(phone_image),
        "detail_image_path": str(detail_image),
        "summary_path": str(summary),
        "run_quality_path": str(run_path / "run_quality.json") if write_quality else None,
        "hold_span_count": len(hold_time_spans),
        "hold_spans": [{"start_s": start, "end_s": end, "duration_s": end - start} for start, end in hold_time_spans],
        "metadata_warnings": list(quality.metadata_warnings),
        "quality": quality.to_dict(),
        "hidden_fault_tail_points": int(len(df) - len(_plot_rows(df))),
        "temperature_sidecar_sample_rows": int(len(temperature)),
    }
    _atomic_write_text(summary, json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate TMA per-run summary images.")
    parser.add_argument("run_dir", help="TMA run folder containing measurement.csv and metadata.json.")
    parser.add_argument("--out", help="Phone summary PNG output path. Defaults to run_summary.png in the run folder.")
    parser.add_argument("--detail-out", help="Detail summary PNG output path. Defaults to run_summary_detail.png in the run folder.")
    parser.add_argument("--summary", help="JSON summary output path. Defaults to run_summary.json in the run folder.")
    parser.add_argument("--no-write-quality", action="store_true", help="Do not update run_quality.json.")
    parser.add_argument("--json", action="store_true", help="Print the summary JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = generate_core_run_plot(
        args.run_dir,
        image_path=args.out,
        detail_image_path=args.detail_out,
        summary_path=args.summary,
        write_quality=not args.no_write_quality,
    )
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Generated TMA run summaries: {summary['image_path']} and {summary['detail_image_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
