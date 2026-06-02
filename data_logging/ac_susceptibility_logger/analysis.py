"""Repeatable AC susceptibility analysis for logger TSV files.

The functions in this module are deliberately UI-free so Codex or a human can
rerun the same analysis from a shell, from tests, or later from a PyPlot panel.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
from typing import Sequence

import numpy as np
import pandas as pd

try:  # matplotlib is a declared runtime dependency, but keep imports lazy-ish.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - exercised only in stripped environments
    plt = None  # type: ignore[assignment]


MU0_H_PER_M = 4.0 * math.pi * 1e-7
OE_A_PER_M = 79.57747154594767

SWEEP_COLUMNS = [
    "timestamp_utc",
    "elapsed_s",
    "setting_index",
    "setting_count",
    "function",
    "frequency_hz",
    "level_mode",
    "level",
    "current_set_a",
    "current_actual_a",
    "voltage_actual_v",
    "psu_resistance_ohm",
    "psu_power_w",
    "direction",
    "repeat_index",
    "lcr_primary",
    "lcr_secondary",
    "lcr_monitor1",
    "lcr_monitor2",
    "lcr_comparator",
    "lcr_raw",
    "psu_backend",
    "psu_resource",
    "psu_status",
    "error",
]

BASELINE_COLUMNS = [
    "timestamp_utc",
    "baseline_setting_index",
    "baseline_repeat_index",
    "frequency_hz",
    "level_mode",
    "level",
    "function",
    "lcr_primary",
    "lcr_secondary",
    "lcr_monitor1",
    "lcr_monitor2",
    "lcr_comparator",
    "lcr_raw",
]

NUMERIC_SWEEP_COLUMNS = [
    "elapsed_s",
    "frequency_hz",
    "level",
    "current_set_a",
    "current_actual_a",
    "voltage_actual_v",
    "psu_resistance_ohm",
    "psu_power_w",
    "lcr_primary",
    "lcr_secondary",
    "lcr_monitor1",
    "lcr_monitor2",
]

NUMERIC_BASELINE_COLUMNS = [
    "frequency_hz",
    "level",
    "lcr_primary",
    "lcr_secondary",
    "lcr_monitor1",
    "lcr_monitor2",
]


@dataclass(frozen=True)
class ExcitationCoilGeometry:
    name: str = "Liptaci excitation coil"
    turns: int = 350
    length_mm: float = 11.0
    inner_diameter_mm: float = 1.3
    outer_diameter_mm: float = 1.7
    wire_diameter_mm: float = 0.05

    @property
    def length_m(self) -> float:
        return self.length_mm / 1000.0

    @property
    def inner_diameter_m(self) -> float:
        return self.inner_diameter_mm / 1000.0

    @property
    def mean_diameter_m(self) -> float:
        return ((self.inner_diameter_mm + self.outer_diameter_mm) / 2.0) / 1000.0

    @property
    def h_oe_per_ampere(self) -> float:
        return (float(self.turns) / self.length_m) / OE_A_PER_M


@dataclass(frozen=True)
class SensingCoilGeometry:
    name: str = "Liptaci sensing coil"
    turns: int = 250
    length_mm: float = 1.0
    inner_diameter_mm: float = 1.7
    outer_diameter_mm: float = 3.6
    wire_diameter_mm: float = 0.05


@dataclass(frozen=True)
class SampleGeometry:
    name: str
    core_diameter_um: float
    glass_diameter_um: float | None = None

    @property
    def core_diameter_m(self) -> float:
        return self.core_diameter_um * 1e-6

    @property
    def glass_diameter_m(self) -> float | None:
        if self.glass_diameter_um is None:
            return None
        return self.glass_diameter_um * 1e-6


@dataclass(frozen=True)
class SusceptibilityAnalysisConfig:
    sweep_files: Sequence[Path]
    baseline_file: Path
    output_dir: Path
    sample: SampleGeometry
    excitation_coil: ExcitationCoilGeometry = ExcitationCoilGeometry()
    sensing_coil: SensingCoilGeometry = SensingCoilGeometry()
    use_inner_diameter_for_filling: bool = True
    low_current_max_mA: float = 20.0
    high_current_min_mA: float = 60.0
    negative_fraction_limit: float = 0.05
    reliable_negative_percent_limit: float = 0.1
    reliable_min_snr: float = 10.0

    @property
    def filling_factor(self) -> float:
        coil_diameter = (
            self.excitation_coil.inner_diameter_m
            if self.use_inner_diameter_for_filling
            else self.excitation_coil.mean_diameter_m
        )
        return (self.sample.core_diameter_m / coil_diameter) ** 2


def _robust_sigma(values: pd.Series | np.ndarray) -> float:
    series = pd.Series(values, dtype="float64").dropna()
    if series.empty:
        return float("nan")
    median = float(series.median())
    mad = float((series - median).abs().median())
    return 1.4826 * mad


def _read_tsv(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", comment="#", header=None, names=list(columns), low_memory=False)


def load_baseline(path: Path) -> pd.DataFrame:
    frame = _read_tsv(path, BASELINE_COLUMNS)
    for column in NUMERIC_BASELINE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[(frame["function"] == "Ls-Rs") & frame["lcr_primary"].notna()].copy()
    frame["excitation_mA"] = frame["level"] * 1000.0
    return frame


def load_sweeps(paths: Sequence[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        frame = _read_tsv(path, SWEEP_COLUMNS)
        frame["source_file"] = path.name
        frames.append(frame)
    if not frames:
        raise ValueError("at least one sweep TSV is required")
    sweep = pd.concat(frames, ignore_index=True)
    for column in NUMERIC_SWEEP_COLUMNS:
        sweep[column] = pd.to_numeric(sweep[column], errors="coerce")
    sweep = sweep[(sweep["function"] == "Ls-Rs") & sweep["error"].isna() & sweep["lcr_primary"].notna()].copy()
    sweep["excitation_mA"] = sweep["level"] * 1000.0
    sweep["current_set_mA"] = (sweep["current_set_a"] * 1000.0).round(6)
    sweep["current_actual_mA"] = sweep["current_actual_a"] * 1000.0
    sweep["ls_nH"] = sweep["lcr_primary"] * 1e9
    return sweep


def summarize_baseline(baseline: pd.DataFrame) -> pd.DataFrame:
    return (
        baseline.groupby(["frequency_hz", "excitation_mA"])
        .agg(
            l_empty_h=("lcr_primary", "median"),
            l_empty_nH=("lcr_primary", lambda s: float(np.nanmedian(s)) * 1e9),
            l_empty_noise_nH=("lcr_primary", lambda s: _robust_sigma(s) * 1e9),
            r_empty_ohm=("lcr_secondary", "median"),
            n_empty=("lcr_primary", "size"),
        )
        .reset_index()
    )


def summarize_sweep_points(sweep: pd.DataFrame, config: SusceptibilityAnalysisConfig) -> pd.DataFrame:
    sweep = sweep.copy()
    sweep["h_ac_oe"] = sweep["level"] * config.excitation_coil.h_oe_per_ampere
    return (
        sweep.groupby(["frequency_hz", "excitation_mA", "h_ac_oe", "direction", "current_set_mA"], dropna=False)
        .agg(
            l_wire_h=("lcr_primary", "median"),
            l_wire_nH=("lcr_primary", lambda s: float(np.nanmedian(s)) * 1e9),
            l_wire_noise_nH=("lcr_primary", lambda s: _robust_sigma(s) * 1e9),
            r_wire_ohm=("lcr_secondary", "median"),
            wire_dc_resistance_ohm=("psu_resistance_ohm", "median"),
            current_actual_mA=("current_actual_mA", "median"),
            n_reads=("lcr_primary", "size"),
            negative_fraction=("lcr_primary", lambda s: float((s < 0).mean())),
        )
        .reset_index()
    )


def compute_apparent_susceptibility(
    points: pd.DataFrame,
    baseline_summary: pd.DataFrame,
    config: SusceptibilityAnalysisConfig,
) -> pd.DataFrame:
    result = points.merge(baseline_summary, on=["frequency_hz", "excitation_mA"], how="left")
    filling = config.filling_factor
    result["filling_factor"] = filling
    result["chi_prime_app"] = (result["l_wire_h"] - result["l_empty_h"]) / (result["l_empty_h"] * filling)
    omega = 2.0 * math.pi * result["frequency_hz"]
    result["chi_double_prime_app"] = (result["r_wire_ohm"] - result["r_empty_ohm"]) / (
        omega * result["l_empty_h"] * filling
    )
    result["delta_l_vs_empty_nH"] = result["l_wire_nH"] - result["l_empty_nH"]
    result["chi_prime_noise"] = (
        np.sqrt(result["l_wire_noise_nH"] ** 2 + result["l_empty_noise_nH"] ** 2) * 1e-9
    ) / (result["l_empty_h"] * filling)
    return result


def compute_change_metrics(points: pd.DataFrame, sweep: pd.DataFrame, config: SusceptibilityAnalysisConfig) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    working = points[points["direction"].isin(["up", "down"])].copy()
    for (frequency, excitation, h_oe), condition in working.groupby(["frequency_hz", "excitation_mA", "h_ac_oe"]):
        raw_condition = sweep[(sweep["frequency_hz"] == frequency) & np.isclose(sweep["excitation_mA"], excitation)]
        negative_percent = float((raw_condition["lcr_primary"] < 0).mean() * 100.0) if len(raw_condition) else float("nan")
        for direction in ("up", "down"):
            direction_points = condition[condition["direction"] == direction].sort_values("current_set_mA")
            direction_points = direction_points[
                (direction_points["negative_fraction"] <= config.negative_fraction_limit)
                & np.isfinite(direction_points["chi_prime_app"])
            ].copy()
            if len(direction_points) < 5:
                continue
            low = direction_points[
                (direction_points["current_set_mA"] >= 15.0)
                & (direction_points["current_set_mA"] <= config.low_current_max_mA)
            ]
            high = direction_points[direction_points["current_set_mA"] >= config.high_current_min_mA]
            if low.empty:
                low = direction_points.head(min(5, len(direction_points)))
            else:
                low = low.head(5)
            if high.empty:
                high = direction_points.tail(min(5, len(direction_points)))
            else:
                high = high.tail(5)
            chi_low = float(np.nanmedian(low["chi_prime_app"]))
            chi_high = float(np.nanmedian(high["chi_prime_app"]))
            dchi = chi_high - chi_low
            chi_noise = float(np.nanmedian(direction_points["chi_prime_noise"]))
            rows.append(
                {
                    "frequency_hz": float(frequency),
                    "excitation_mA": float(excitation),
                    "h_ac_oe": float(h_oe),
                    "direction": direction,
                    "chi_prime_low_window": chi_low,
                    "chi_prime_high_window": chi_high,
                    "delta_chi_prime_high_minus_low": dchi,
                    "abs_delta_chi_prime": abs(dchi),
                    "percent_change_vs_low": dchi / abs(chi_low) * 100.0 if chi_low else float("nan"),
                    "chi_prime_noise": chi_noise,
                    "chi_prime_snr": abs(dchi) / (chi_noise * math.sqrt(2.0)) if chi_noise > 0 else float("nan"),
                    "negative_ls_percent_raw": negative_percent,
                    "low_current_min_mA": float(low["current_set_mA"].min()),
                    "low_current_max_mA": float(low["current_set_mA"].max()),
                    "high_current_min_mA": float(high["current_set_mA"].min()),
                    "high_current_max_mA": float(high["current_set_mA"].max()),
                }
            )
    return pd.DataFrame(rows)


def rank_conditions(metrics: pd.DataFrame, config: SusceptibilityAnalysisConfig) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame()
    paired = metrics.pivot_table(
        index=["frequency_hz", "excitation_mA", "h_ac_oe"],
        columns="direction",
        values=[
            "delta_chi_prime_high_minus_low",
            "chi_prime_low_window",
            "chi_prime_high_window",
            "abs_delta_chi_prime",
            "percent_change_vs_low",
            "chi_prime_noise",
            "chi_prime_snr",
            "negative_ls_percent_raw",
        ],
        aggfunc="first",
    )
    paired.columns = [f"{name}_{direction}" for name, direction in paired.columns]
    paired = paired.reset_index()
    paired["mean_abs_delta_chi_prime"] = paired[["abs_delta_chi_prime_up", "abs_delta_chi_prime_down"]].mean(axis=1)
    paired["mean_abs_chi_prime_low_window"] = (
        paired[["chi_prime_low_window_up", "chi_prime_low_window_down"]].abs().mean(axis=1)
    )
    paired["mean_chi_prime_high_window"] = paired[["chi_prime_high_window_up", "chi_prime_high_window_down"]].mean(axis=1)
    paired["mean_chi_prime_snr"] = paired[["chi_prime_snr_up", "chi_prime_snr_down"]].mean(axis=1)
    paired["mean_abs_percent_change_vs_low"] = (
        paired[["percent_change_vs_low_up", "percent_change_vs_low_down"]].abs().mean(axis=1)
    )
    paired["negative_ls_percent"] = paired[["negative_ls_percent_raw_up", "negative_ls_percent_raw_down"]].mean(axis=1)
    paired["literature_field_range"] = paired["h_ac_oe"].between(1.0, 10.0)
    paired["recommended_quality"] = (
        (paired["negative_ls_percent"] < config.reliable_negative_percent_limit)
        & (paired["mean_chi_prime_snr"] >= config.reliable_min_snr)
    )
    return paired.sort_values(
        ["recommended_quality", "literature_field_range", "mean_chi_prime_snr", "mean_abs_delta_chi_prime"],
        ascending=[False, False, False, False],
    )


def export_origin_ready_tables(points: pd.DataFrame, output_dir: Path) -> None:
    base_columns = ["frequency_hz", "excitation_mA", "h_ac_oe", "direction", "current_set_mA", "current_actual_mA"]
    prime = points[
        base_columns
        + [
            "chi_prime_app",
            "chi_prime_noise",
            "l_wire_nH",
            "l_empty_nH",
            "wire_dc_resistance_ohm",
            "r_wire_ohm",
            "r_empty_ohm",
        ]
    ].copy()
    loss = points[base_columns + ["chi_double_prime_app", "wire_dc_resistance_ohm", "r_wire_ohm", "r_empty_ohm"]].copy()
    prime.to_csv(output_dir / "origin_chi_prime_curves.csv", index=False)
    loss.to_csv(output_dir / "origin_chi_double_prime_curves.csv", index=False)


def export_condition_summary_for_origin(ranking: pd.DataFrame, output_dir: Path) -> None:
    if ranking.empty:
        return
    columns = [
        "frequency_hz",
        "frequency_label",
        "frequency_khz",
        "excitation_mA",
        "h_ac_oe",
        "mean_abs_delta_chi_prime",
        "mean_abs_chi_prime_low_window",
        "mean_chi_prime_high_window",
        "mean_chi_prime_snr",
        "mean_abs_percent_change_vs_low",
        "negative_ls_percent",
        "recommended_quality",
        "literature_field_range",
    ]
    summary = ranking.copy()
    summary["frequency_label"] = summary["frequency_hz"].map(format_frequency)
    summary["frequency_khz"] = summary["frequency_hz"] / 1000.0
    summary[columns].to_csv(output_dir / "origin_condition_summary.csv", index=False)


def _format_float(value: float, digits: int = 3) -> str:
    if not math.isfinite(float(value)):
        return ""
    return f"{float(value):.{digits}g}"


def format_frequency(value_hz: float) -> str:
    value = float(value_hz)
    if abs(value) >= 1000.0:
        return f"{_format_float(value / 1000.0)} kHz"
    return f"{_format_float(value)} Hz"


def _write_markdown_report(ranking: pd.DataFrame, config: SusceptibilityAnalysisConfig, output_dir: Path) -> None:
    lines = [
        "# AC Susceptibility Analysis",
        "",
        f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Inputs",
        "",
        f"- Baseline: `{config.baseline_file}`",
    ]
    lines.extend(f"- Sweep: `{path}`" for path in config.sweep_files)
    lines.extend(
        [
            "",
            "## Geometry",
            "",
            f"- Sample: `{config.sample.name}`",
            f"- Metallic core diameter: `{config.sample.core_diameter_um:g} um`",
            f"- Glass diameter: `{config.sample.glass_diameter_um:g} um`"
            if config.sample.glass_diameter_um is not None
            else "- Glass diameter: not set",
            f"- Excitation coil: `{config.excitation_coil.turns}` turns, `{config.excitation_coil.length_mm:g} mm` long, "
            f"`{config.excitation_coil.inner_diameter_mm:g} mm` ID",
            f"- Sensing coil metadata: `{config.sensing_coil.turns}` turns, `{config.sensing_coil.length_mm:g} mm` long, "
            f"`{config.sensing_coil.inner_diameter_mm:g} mm` ID",
            f"- Filling factor: `{config.filling_factor:.6g}`",
            "",
            "## Formula",
            "",
            "`chi_prime_app = (L_wire - L_empty) / (L_empty * filling_factor)`",
            "",
            "`chi_double_prime_app = (R_wire - R_empty) / (2*pi*f*L_empty*filling_factor)`",
            "",
            "## Recommended Conditions",
            "",
            "| Frequency | Excitation | H_ac | mean abs delta chi_prime | SNR | approx percent |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    recommended = ranking[ranking["recommended_quality"] & ranking["literature_field_range"]]
    for _, row in recommended.head(12).iterrows():
        lines.append(
            "| "
            f"{format_frequency(row.frequency_hz)} | "
            f"{row.excitation_mA:g} mA | "
            f"{row.h_ac_oe:.2f} Oe | "
            f"{_format_float(row.mean_abs_delta_chi_prime)} | "
            f"{row.mean_chi_prime_snr:.1f} | "
            f"{row.mean_abs_percent_change_vs_low:.1f}% |"
        )
    high_percent = ranking[
        np.isfinite(ranking["mean_abs_percent_change_vs_low"])
        & (ranking["mean_abs_percent_change_vs_low"] >= 500.0)
    ].sort_values("mean_abs_percent_change_vs_low", ascending=False)
    lines.extend(
        [
            "",
            "The percent column is normalized by the low-current apparent susceptibility window. "
            "Very high percentages appear when that low-current denominator is small, crosses near zero, or is noisy. "
            "For choosing report conditions, prefer the actual chi' curves, mean abs delta chi', and SNR.",
        ]
    )
    if not high_percent.empty:
        lines.extend(
            [
                "",
                "## High Percent Conditions",
                "",
                "| Frequency | Excitation | H_ac | mean low-window abs chi_prime | mean abs delta chi_prime | approx percent | SNR |",
                "|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, row in high_percent.head(12).iterrows():
            lines.append(
                "| "
                f"{format_frequency(row.frequency_hz)} | "
                f"{row.excitation_mA:g} mA | "
                f"{row.h_ac_oe:.2f} Oe | "
                f"{_format_float(row.mean_abs_chi_prime_low_window)} | "
                f"{_format_float(row.mean_abs_delta_chi_prime)} | "
                f"{row.mean_abs_percent_change_vs_low:.1f}% | "
                f"{row.mean_chi_prime_snr:.1f} |"
            )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `apparent_complex_susceptibility_points.csv`",
            "- `apparent_susceptibility_change_by_direction.csv`",
            "- `apparent_susceptibility_condition_ranking.csv`",
            "- `origin_condition_summary.csv`",
            "- `origin_chi_prime_curves.csv`",
            "- `origin_chi_double_prime_curves.csv`",
            "- `recommended_chi_prime_curves.png`",
            "- `recommended_chi_double_prime_curves.png`",
            "- `top_complex_susceptibility_curves.png`",
            "- `high_percent_chi_prime_curves.png`",
            "- `all_conditions_delta_chi_curves_grid.png`",
            "- `all_conditions_delta_chi_heatmap.png`",
            "- `all_conditions_snr_heatmap.png`",
            "- `all_conditions_percent_heatmap.png`",
        ]
    )
    (output_dir / "SUSCEPTIBILITY_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_recommended_curves(points: pd.DataFrame, ranking: pd.DataFrame, output_dir: Path) -> None:
    if plt is None:
        return
    recommended = ranking[ranking["recommended_quality"] & ranking["literature_field_range"]]
    selection = recommended.sort_values(["mean_chi_prime_snr", "mean_abs_delta_chi_prime"], ascending=False).head(6)
    if selection.empty:
        selection = ranking.sort_values(["mean_chi_prime_snr", "mean_abs_delta_chi_prime"], ascending=False).head(6)
    _plot_component(points, selection, "chi_prime_app", "apparent chi'", output_dir / "recommended_chi_prime_curves.png")
    _plot_component(
        points,
        selection,
        "chi_double_prime_app",
        "apparent chi''",
        output_dir / "recommended_chi_double_prime_curves.png",
    )
    _plot_complex(points, selection.head(3), output_dir / "top_complex_susceptibility_curves.png")
    high_percent = ranking[
        np.isfinite(ranking["mean_abs_percent_change_vs_low"])
        & (ranking["mean_abs_percent_change_vs_low"] >= 500.0)
    ].sort_values("mean_abs_percent_change_vs_low", ascending=False)
    if not high_percent.empty:
        _plot_component(
            points,
            high_percent.head(6),
            "chi_prime_app",
            "apparent chi'",
            output_dir / "high_percent_chi_prime_curves.png",
            include_percent=True,
        )
    _plot_delta_chi_curve_grid(points, ranking, output_dir)
    _plot_condition_heatmaps(ranking, output_dir)


def _clean_component_points(points: pd.DataFrame, row: pd.Series, component: str, config_negative_limit: float = 0.05) -> pd.DataFrame:
    data = points[
        (points["frequency_hz"] == row.frequency_hz)
        & np.isclose(points["excitation_mA"], row.excitation_mA)
        & points["direction"].isin(["up", "down"])
    ].copy()
    data = data[(data["negative_fraction"] <= config_negative_limit) & np.isfinite(data[component])]
    return data.sort_values("current_set_mA")


def _set_tight_y_limits(axis: object, values: pd.Series) -> None:
    clean = pd.Series(values, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return
    y_min = float(clean.min())
    y_max = float(clean.max())
    span = y_max - y_min
    if not math.isfinite(span):
        return
    if span <= 0:
        margin = max(abs(y_min) * 0.05, 1.0)
    else:
        margin = max(span * 0.08, abs(y_max) * 0.01, 1e-12)
    lower = y_min - margin
    upper = y_max + margin
    axis.set_ylim(lower, upper)
    if lower <= 0.0 <= upper:
        axis.axhline(0.0, color="0.35", linewidth=0.6)


def _plot_direction_lines(axis: object, data: pd.DataFrame, y_column: str) -> None:
    for direction, marker in (("up", "o"), ("down", "s")):
        direction_data = data[data["direction"] == direction]
        axis.plot(
            direction_data["current_set_mA"],
            direction_data[y_column],
            marker=marker,
            markersize=3,
            linewidth=1.2,
            label=direction,
        )


def _condition_title(row: pd.Series, *, include_delta: bool = True, include_percent: bool = False) -> str:
    title = f"{format_frequency(row.frequency_hz)}, {row.excitation_mA:g} mA ({row.h_ac_oe:.2f} Oe)"
    details: list[str] = []
    if include_delta:
        details.append(f"dchi'={row.mean_abs_delta_chi_prime:.3g}")
    if include_percent:
        details.append(f"{row.mean_abs_percent_change_vs_low:.1f}% vs low chi'")
        if "mean_abs_chi_prime_low_window" in row:
            details.append(f"low |chi'|={row.mean_abs_chi_prime_low_window:.3g}")
    if "mean_chi_prime_snr" in row:
        details.append(f"SNR={row.mean_chi_prime_snr:.1f}")
    if details:
        separator = "\n" if include_percent else ", "
        title = f"{title}{separator}" + ", ".join(details)
    return title


def _plot_component(
    points: pd.DataFrame,
    selection: pd.DataFrame,
    component: str,
    label: str,
    path: Path,
    *,
    include_percent: bool = False,
) -> None:
    if selection.empty:
        return
    height_per_panel = 3.1 if include_percent else 2.8
    fig, axes = plt.subplots(
        len(selection), 2, figsize=(12, height_per_panel * len(selection)), dpi=160, sharex=True
    )
    if len(selection) == 1:
        axes = np.array([axes])
    for row_index, (_, row) in enumerate(selection.iterrows()):
        data = _clean_component_points(points, row, component)
        susc_axis = axes[row_index, 0]
        resistance_axis = axes[row_index, 1]
        _plot_direction_lines(susc_axis, data, component)
        _set_tight_y_limits(susc_axis, data[component])
        susc_axis.set_ylabel(label)
        susc_axis.set_title(
            _condition_title(row, include_percent=include_percent), fontsize=10 if include_percent else None
        )
        susc_axis.legend(fontsize=8)
        _plot_direction_lines(resistance_axis, data, "wire_dc_resistance_ohm")
        _set_tight_y_limits(resistance_axis, data["wire_dc_resistance_ohm"])
        resistance_axis.set_ylabel("DC wire R [ohm]")
        resistance_axis.set_title("DC wire resistance")
        resistance_axis.legend(fontsize=8)
    for axis in axes[-1, :]:
        axis.set_xlabel("DC heating current set [mA]")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _plot_complex(points: pd.DataFrame, selection: pd.DataFrame, path: Path) -> None:
    if selection.empty:
        return
    fig, axes = plt.subplots(len(selection), 3, figsize=(15, 3.2 * len(selection)), dpi=160, sharex=True)
    if len(selection) == 1:
        axes = np.array([axes])
    for row_index, (_, row) in enumerate(selection.iterrows()):
        for col_index, (component, label) in enumerate(
            (("chi_prime_app", "apparent chi'"), ("chi_double_prime_app", "apparent chi''"))
        ):
            axis = axes[row_index, col_index]
            data = _clean_component_points(points, row, component)
            _plot_direction_lines(axis, data, component)
            _set_tight_y_limits(axis, data[component])
            axis.set_ylabel(label)
            axis.set_title(_condition_title(row, include_delta=False))
            axis.legend(fontsize=8)
        resistance_axis = axes[row_index, 2]
        resistance_data = _clean_component_points(points, row, "wire_dc_resistance_ohm")
        _plot_direction_lines(resistance_axis, resistance_data, "wire_dc_resistance_ohm")
        _set_tight_y_limits(resistance_axis, resistance_data["wire_dc_resistance_ohm"])
        resistance_axis.set_ylabel("DC wire R [ohm]")
        resistance_axis.set_title("DC wire resistance")
        resistance_axis.legend(fontsize=8)
    for axis in axes[-1, :]:
        axis.set_xlabel("DC heating current set [mA]")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _plot_delta_chi_curve_grid(points: pd.DataFrame, ranking: pd.DataFrame, output_dir: Path) -> None:
    if ranking.empty:
        return
    frequencies = sorted((float(value) for value in ranking["frequency_hz"].dropna().unique()), reverse=True)
    excitations = sorted(float(value) for value in ranking["excitation_mA"].dropna().unique())
    if not frequencies or not excitations:
        return
    clean_points = points[
        points["direction"].isin(["up", "down"])
        & (points["negative_fraction"] <= 0.05)
        & np.isfinite(points["chi_prime_app"])
    ].copy()
    ranking_lookup = {
        (float(row.frequency_hz), float(row.excitation_mA)): row
        for row in ranking.itertuples(index=False)
    }
    fig_width = max(11.0, 1.55 * len(excitations) + 2.4)
    fig_height = max(12.0, 0.92 * len(frequencies) + 2.0)
    fig, axes = plt.subplots(
        len(frequencies),
        len(excitations),
        figsize=(fig_width, fig_height),
        dpi=180,
        sharex=True,
        sharey=False,
    )
    if len(frequencies) == 1:
        axes = np.array([axes])
    if len(excitations) == 1:
        axes = axes.reshape(len(frequencies), 1)
    for y_index, frequency in enumerate(frequencies):
        for x_index, excitation in enumerate(excitations):
            axis = axes[y_index, x_index]
            data = clean_points[
                (clean_points["frequency_hz"] == frequency)
                & np.isclose(clean_points["excitation_mA"], excitation)
            ].sort_values("current_set_mA")
            ranking_row = ranking_lookup.get((frequency, excitation))
            if ranking_row is not None and np.isfinite(ranking_row.mean_abs_percent_change_vs_low):
                percent = float(ranking_row.mean_abs_percent_change_vs_low)
                percent_text = f"{percent:.0f}%" if percent >= 100.0 else f"{percent:.1f}%"
                axis.text(
                    0.03,
                    0.92,
                    percent_text,
                    ha="left",
                    va="top",
                    transform=axis.transAxes,
                    fontsize=6,
                    color="0.2",
                    bbox={"facecolor": "white", "alpha": 0.72, "edgecolor": "none", "pad": 0.8},
                )
            if data.empty:
                axis.set_facecolor("#e0e0e0")
                axis.text(0.5, 0.5, "filtered", ha="center", va="center", transform=axis.transAxes, fontsize=6)
            else:
                plotted = []
                for direction, marker_size in (("up", 1.8), ("down", 1.8)):
                    direction_data = data[data["direction"] == direction].copy()
                    if direction_data.empty:
                        continue
                    low = direction_data[
                        (direction_data["current_set_mA"] >= 15.0)
                        & (direction_data["current_set_mA"] <= 20.0)
                    ]
                    if low.empty:
                        low = direction_data.head(min(5, len(direction_data)))
                    baseline = float(np.nanmedian(low["chi_prime_app"]))
                    direction_data["delta_chi_prime_app"] = direction_data["chi_prime_app"] - baseline
                    display_data = _filter_delta_curve_outliers(direction_data)
                    plotted.append(display_data["delta_chi_prime_app"])
                    axis.plot(
                        display_data["current_set_mA"],
                        display_data["delta_chi_prime_app"],
                        linewidth=0.8,
                        marker="o",
                        markersize=marker_size,
                        label=direction,
                    )
                if plotted:
                    _set_robust_y_limits(axis, pd.concat(plotted, ignore_index=True))
                axis.axhline(0.0, color="0.5", linewidth=0.4)
            axis.tick_params(axis="both", labelsize=5, length=2, pad=1)
            if y_index == 0:
                axis.set_title(f"{excitation:g} mA", fontsize=8)
            if x_index == 0:
                axis.set_ylabel(format_frequency(frequency), fontsize=8)
            else:
                axis.set_yticklabels([])
            if y_index != len(frequencies) - 1:
                axis.set_xticklabels([])
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", fontsize=8, frameon=False)
    fig.suptitle("Delta apparent chi' vs DC current for all measured conditions", fontsize=13)
    fig.supxlabel("LCR excitation current columns; x-axis inside each cell is DC heating current [mA]", fontsize=9)
    fig.supylabel("Frequency rows; y-axis is delta chi' vs low-current baseline; labels show approx percent change", fontsize=9)
    fig.tight_layout(rect=(0.04, 0.04, 0.98, 0.96))
    fig.savefig(output_dir / "all_conditions_delta_chi_curves_grid.png")
    plt.close(fig)


def _filter_delta_curve_outliers(direction_data: pd.DataFrame) -> pd.DataFrame:
    if len(direction_data) < 8:
        return direction_data
    working = direction_data.sort_values("current_set_mA").copy()
    values = working["delta_chi_prime_app"].astype("float64")
    rolling = values.rolling(window=5, center=True, min_periods=3).median()
    rolling = rolling.fillna(values.rolling(window=3, center=True, min_periods=1).median())
    residual = values - rolling
    residual_mad = _robust_sigma(residual)
    central_range = float(values.quantile(0.9) - values.quantile(0.1))
    threshold = max(residual_mad * 7.0 if math.isfinite(residual_mad) else 0.0, central_range * 0.35, 10.0)
    filtered = working[residual.abs() <= threshold].copy()
    if len(filtered) < max(4, len(working) * 0.6):
        return working
    return filtered


def _set_robust_y_limits(axis: object, values: pd.Series) -> None:
    clean = pd.Series(values, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return
    if len(clean) >= 8:
        lower = float(clean.quantile(0.03))
        upper = float(clean.quantile(0.97))
    else:
        lower = float(clean.min())
        upper = float(clean.max())
    if not math.isfinite(lower) or not math.isfinite(upper):
        return
    if lower == upper:
        margin = max(abs(lower) * 0.05, 1.0)
    else:
        margin = max((upper - lower) * 0.12, 1.0)
    axis.set_ylim(lower - margin, upper + margin)


def _plot_condition_heatmaps(ranking: pd.DataFrame, output_dir: Path) -> None:
    if ranking.empty:
        return
    delta_data = ranking.copy()
    delta_data["delta_plot_value"] = np.log10(delta_data["mean_abs_delta_chi_prime"].clip(lower=1.0))
    _plot_condition_heatmap(
        delta_data,
        "delta_plot_value",
        "log10 mean abs delta chi'",
        output_dir / "all_conditions_delta_chi_heatmap.png",
        annotate_column="mean_abs_delta_chi_prime",
    )
    _plot_condition_heatmap(
        ranking,
        "mean_chi_prime_snr",
        "SNR",
        output_dir / "all_conditions_snr_heatmap.png",
    )
    percent_data = ranking.copy()
    percent_data["percent_plot_value"] = np.log10(percent_data["mean_abs_percent_change_vs_low"].clip(lower=1.0))
    _plot_condition_heatmap(
        percent_data,
        "percent_plot_value",
        "log10 approx percent vs low chi'",
        output_dir / "all_conditions_percent_heatmap.png",
        annotate_column="mean_abs_percent_change_vs_low",
        annotation_suffix="%",
    )


def _plot_condition_heatmap(
    ranking: pd.DataFrame,
    value_column: str,
    title: str,
    path: Path,
    *,
    annotate_column: str | None = None,
    annotation_suffix: str = "",
) -> None:
    if ranking.empty or value_column not in ranking.columns:
        return
    frequencies = sorted(float(value) for value in ranking["frequency_hz"].dropna().unique())
    excitations = sorted(float(value) for value in ranking["excitation_mA"].dropna().unique())
    if not frequencies or not excitations:
        return
    values = np.full((len(frequencies), len(excitations)), np.nan)
    annotations = np.full((len(frequencies), len(excitations)), np.nan)
    for row in ranking.itertuples(index=False):
        y = frequencies.index(float(row.frequency_hz))
        x = excitations.index(float(row.excitation_mA))
        values[y, x] = float(getattr(row, value_column))
        if annotate_column is not None:
            annotations[y, x] = float(getattr(row, annotate_column))
    fig_width = max(7.0, 0.9 * len(excitations) + 3.0)
    fig_height = max(5.0, 0.42 * len(frequencies) + 2.0)
    fig, axis = plt.subplots(figsize=(fig_width, fig_height), dpi=170)
    colormap = plt.get_cmap("viridis").copy()
    colormap.set_bad("#d9d9d9")
    image = axis.imshow(np.ma.masked_invalid(values), aspect="auto", origin="lower", cmap=colormap)
    axis.set_xticks(range(len(excitations)))
    axis.set_xticklabels([f"{value:g}" for value in excitations], rotation=45, ha="right")
    axis.set_yticks(range(len(frequencies)))
    axis.set_yticklabels([format_frequency(value) for value in frequencies])
    axis.set_xlabel("LCR excitation current [mA]")
    axis.set_ylabel("Frequency")
    axis.set_title(title)
    colorbar = fig.colorbar(image, ax=axis)
    colorbar.set_label(title)
    for y, frequency in enumerate(frequencies):
        for x, excitation in enumerate(excitations):
            raw_value = annotations[y, x] if annotate_column is not None else values[y, x]
            if not np.isfinite(raw_value):
                axis.text(x, y, "filtered", ha="center", va="center", color="0.25", fontsize=6)
                continue
            text = f"{raw_value:.0f}{annotation_suffix}" if abs(raw_value) >= 100 else f"{raw_value:.1f}{annotation_suffix}"
            axis.text(x, y, text, ha="center", va="center", color="white", fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def run_analysis(config: SusceptibilityAnalysisConfig) -> dict[str, Path]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    baseline = load_baseline(config.baseline_file)
    sweep = load_sweeps(config.sweep_files)
    baseline_summary = summarize_baseline(baseline)
    point_summary = summarize_sweep_points(sweep, config)
    susceptibility = compute_apparent_susceptibility(point_summary, baseline_summary, config)
    metrics = compute_change_metrics(susceptibility, sweep, config)
    ranking = rank_conditions(metrics, config)

    baseline_summary.to_csv(config.output_dir / "empty_coil_baseline_summary.csv", index=False)
    point_summary.to_csv(config.output_dir / "point_medians.csv", index=False)
    susceptibility.to_csv(config.output_dir / "apparent_complex_susceptibility_points.csv", index=False)
    metrics.to_csv(config.output_dir / "apparent_susceptibility_change_by_direction.csv", index=False)
    ranking.to_csv(config.output_dir / "apparent_susceptibility_condition_ranking.csv", index=False)
    export_origin_ready_tables(susceptibility, config.output_dir)
    export_condition_summary_for_origin(ranking, config.output_dir)
    plot_recommended_curves(susceptibility, ranking, config.output_dir)
    _write_markdown_report(ranking, config, config.output_dir)

    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "sweep_files": [str(path) for path in config.sweep_files],
            "baseline_file": str(config.baseline_file),
            "output_dir": str(config.output_dir),
            "sample": asdict(config.sample),
            "excitation_coil": asdict(config.excitation_coil),
            "sensing_coil": asdict(config.sensing_coil),
            "filling_factor": config.filling_factor,
            "h_oe_per_ampere": config.excitation_coil.h_oe_per_ampere,
        },
    }
    (config.output_dir / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "output_dir": config.output_dir,
        "report": config.output_dir / "SUSCEPTIBILITY_REPORT.md",
        "ranking": config.output_dir / "apparent_susceptibility_condition_ranking.csv",
        "chi_prime_plot": config.output_dir / "recommended_chi_prime_curves.png",
        "high_percent_plot": config.output_dir / "high_percent_chi_prime_curves.png",
        "delta_heatmap": config.output_dir / "all_conditions_delta_chi_heatmap.png",
        "snr_heatmap": config.output_dir / "all_conditions_snr_heatmap.png",
        "percent_heatmap": config.output_dir / "all_conditions_percent_heatmap.png",
        "complex_plot": config.output_dir / "top_complex_susceptibility_curves.png",
        "delta_curve_grid": config.output_dir / "all_conditions_delta_chi_curves_grid.png",
    }


def copy_preview_images(outputs: dict[str, Path], preview_dir: Path) -> dict[str, Path]:
    preview_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    for key in (
        "chi_prime_plot",
        "complex_plot",
        "high_percent_plot",
        "delta_curve_grid",
        "delta_heatmap",
        "snr_heatmap",
        "percent_heatmap",
    ):
        path = outputs.get(key)
        if path is None or not path.exists():
            continue
        destination = preview_dir / path.name
        if destination.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            destination = preview_dir / f"{path.stem}_{timestamp}{path.suffix}"
        try:
            shutil.copy2(path, destination)
        except OSError:
            continue
        copied[key] = destination
    return copied


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze AC susceptibility logger TSV files.")
    parser.add_argument("--sweep", action="append", required=True, type=Path, help="Microwire sweep TSV. Repeatable.")
    parser.add_argument("--baseline", required=True, type=Path, help="Empty-coil baseline TSV.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Output analysis folder.")
    parser.add_argument("--sample-name", default="microwire", help="Sample name for metadata.")
    parser.add_argument("--core-diameter-um", required=True, type=float, help="Metallic core diameter in micrometers.")
    parser.add_argument("--glass-diameter-um", type=float, help="Glass outer diameter in micrometers.")
    parser.add_argument("--coil-turns", default=350, type=int)
    parser.add_argument("--coil-length-mm", default=11.0, type=float)
    parser.add_argument("--coil-inner-diameter-mm", default=1.3, type=float)
    parser.add_argument("--coil-outer-diameter-mm", default=1.7, type=float)
    parser.add_argument("--preview-dir", type=Path, help="Optional ASCII/simple folder for copied preview PNGs.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = SusceptibilityAnalysisConfig(
        sweep_files=tuple(args.sweep),
        baseline_file=args.baseline,
        output_dir=args.out_dir,
        sample=SampleGeometry(
            name=args.sample_name,
            core_diameter_um=args.core_diameter_um,
            glass_diameter_um=args.glass_diameter_um,
        ),
        excitation_coil=ExcitationCoilGeometry(
            turns=args.coil_turns,
            length_mm=args.coil_length_mm,
            inner_diameter_mm=args.coil_inner_diameter_mm,
            outer_diameter_mm=args.coil_outer_diameter_mm,
        ),
    )
    outputs = run_analysis(config)
    print(f"Output directory: {outputs['output_dir']}")
    print(f"Report: {outputs['report']}")
    if args.preview_dir is not None:
        copied = copy_preview_images(outputs, args.preview_dir)
        for key, path in copied.items():
            print(f"Preview {key}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
