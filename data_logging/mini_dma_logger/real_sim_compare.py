"""Compare real TMA runs with software-only simulator outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class NormalizedPoint:
    elapsed_s: float
    stress_mpa: float | None
    target_mpa: float | None
    strain_pct: float | None
    current_ma: float | None
    phase: str
    hold_active: bool


def _number(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _boolish(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_measurement_points(path: Path) -> list[NormalizedPoint]:
    """Load real or simulated TMA measurement rows into a common shape."""

    points: list[NormalizedPoint] = []
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        for row in csv.DictReader(handle):
            elapsed = _number(row.get("elapsed_s"))
            if elapsed is None:
                continue
            phase = (row.get("automation_phase") or "").strip()
            target = _number(row.get("target_stress_mpa"))
            if target is None:
                target = _number(row.get("automation_target_value"))
            current = _number(row.get("current_measured_mA"))
            if current is None:
                current = _number(row.get("current_set_mA") or row.get("current_ma"))
            points.append(
                NormalizedPoint(
                    elapsed_s=elapsed,
                    stress_mpa=_number(row.get("processed_center_mpa") or row.get("stress_mpa")),
                    target_mpa=target,
                    strain_pct=_number(row.get("strain_pct")),
                    current_ma=current,
                    phase=phase,
                    hold_active=phase == "current_hold" or _boolish(row.get("current_hold_active")),
                )
            )
    points.sort(key=lambda point: point.elapsed_s)
    return points


def summarize_points(points: list[NormalizedPoint]) -> dict[str, object]:
    strain = [point.strain_pct for point in points if point.strain_pct is not None]
    stress = [point.stress_mpa for point in points if point.stress_mpa is not None]
    current = [point.current_ma for point in points if point.current_ma is not None]
    errors = [
        abs(point.stress_mpa - point.target_mpa)
        for point in points
        if point.stress_mpa is not None and point.target_mpa is not None
    ]
    hold_groups = _hold_groups(points)
    hold_strain_spans = [_span([point.strain_pct for point in group if point.strain_pct is not None]) for group in hold_groups]
    total_s = (points[-1].elapsed_s - points[0].elapsed_s) if len(points) >= 2 else 0.0
    hold_rows = sum(1 for point in points if point.hold_active)
    return {
        "row_count": len(points),
        "total_elapsed_s": total_s,
        "strain_min_pct": min(strain) if strain else None,
        "strain_max_pct": max(strain) if strain else None,
        "strain_span_pct": _span(strain),
        "stress_min_mpa": min(stress) if stress else None,
        "stress_max_mpa": max(stress) if stress else None,
        "current_min_mA": min(current) if current else None,
        "current_max_mA": max(current) if current else None,
        "hold_row_fraction": hold_rows / len(points) if points else 0.0,
        "hold_group_count": len(hold_groups),
        "max_hold_strain_span_pct": max(hold_strain_spans, default=0.0),
        "p95_abs_stress_error_mpa": _percentile(errors, 0.95),
        "max_abs_stress_error_mpa": max(errors, default=0.0),
    }


def compare_measurements(
    real_csv: Path,
    sim_csv: Path,
    output_dir: Path,
    *,
    max_target_mpa: float | None = None,
    target_tolerance_mpa: float = 0.0,
) -> dict[str, Path]:
    """Write real-vs-sim comparison artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    real_points = load_measurement_points(real_csv)
    sim_points = load_measurement_points(sim_csv)
    if max_target_mpa is not None:
        real_points = _filter_by_max_target(real_points, max_target_mpa, target_tolerance_mpa)
        sim_points = _filter_by_max_target(sim_points, max_target_mpa, target_tolerance_mpa)
    summary = {
        "real_csv": str(real_csv),
        "sim_csv": str(sim_csv),
        "max_target_mpa": max_target_mpa,
        "target_tolerance_mpa": target_tolerance_mpa,
        "real": summarize_points(real_points),
        "simulation": summarize_points(sim_points),
        "delta": _delta_summary(summarize_points(real_points), summarize_points(sim_points)),
    }
    summary_path = output_dir / "real_vs_sim_summary.json"
    report_path = output_dir / "real_vs_sim_report.md"
    plot_path = output_dir / "real_vs_sim.png"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_report(summary), encoding="utf-8")
    _write_plot(plot_path, real_points, sim_points)
    return {"summary": summary_path, "report": report_path, "plot": plot_path}


def _delta_summary(real: dict[str, object], sim: dict[str, object]) -> dict[str, float | None]:
    keys = (
        "total_elapsed_s",
        "strain_span_pct",
        "current_max_mA",
        "hold_row_fraction",
        "max_hold_strain_span_pct",
        "p95_abs_stress_error_mpa",
        "max_abs_stress_error_mpa",
    )
    delta: dict[str, float | None] = {}
    for key in keys:
        real_value = _number(real.get(key))
        sim_value = _number(sim.get(key))
        delta[f"{key}_sim_minus_real"] = None if real_value is None or sim_value is None else sim_value - real_value
    return delta


def _filter_by_max_target(
    points: list[NormalizedPoint],
    max_target_mpa: float,
    target_tolerance_mpa: float,
) -> list[NormalizedPoint]:
    threshold = max_target_mpa + max(0.0, target_tolerance_mpa)
    return [
        point
        for point in points
        if point.target_mpa is None or point.target_mpa <= threshold
    ]


def _span(values: Iterable[float | None]) -> float | None:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return max(numeric) - min(numeric)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _hold_groups(points: list[NormalizedPoint]) -> list[list[NormalizedPoint]]:
    groups: list[list[NormalizedPoint]] = []
    active: list[NormalizedPoint] = []
    for point in points:
        if point.hold_active:
            active.append(point)
        elif active:
            groups.append(active)
            active = []
    if active:
        groups.append(active)
    return groups


def _report(summary: dict[str, object]) -> str:
    real = summary["real"]
    sim = summary["simulation"]
    delta = summary["delta"]
    assert isinstance(real, dict)
    assert isinstance(sim, dict)
    assert isinstance(delta, dict)
    return f"""# TMA real-vs-simulation comparison

Real CSV: `{summary["real_csv"]}`

Simulation CSV: `{summary["sim_csv"]}`

| Metric | Real | Simulation | Sim - real |
| --- | ---: | ---: | ---: |
| Total elapsed s | {_fmt(real.get("total_elapsed_s"))} | {_fmt(sim.get("total_elapsed_s"))} | {_fmt(delta.get("total_elapsed_s_sim_minus_real"))} |
| Strain span % | {_fmt(real.get("strain_span_pct"))} | {_fmt(sim.get("strain_span_pct"))} | {_fmt(delta.get("strain_span_pct_sim_minus_real"))} |
| Max current mA | {_fmt(real.get("current_max_mA"))} | {_fmt(sim.get("current_max_mA"))} | {_fmt(delta.get("current_max_mA_sim_minus_real"))} |
| Hold row fraction | {_fmt(real.get("hold_row_fraction"))} | {_fmt(sim.get("hold_row_fraction"))} | {_fmt(delta.get("hold_row_fraction_sim_minus_real"))} |
| Max hold strain span % | {_fmt(real.get("max_hold_strain_span_pct"))} | {_fmt(sim.get("max_hold_strain_span_pct"))} | {_fmt(delta.get("max_hold_strain_span_pct_sim_minus_real"))} |
| P95 abs stress error MPa | {_fmt(real.get("p95_abs_stress_error_mpa"))} | {_fmt(sim.get("p95_abs_stress_error_mpa"))} | {_fmt(delta.get("p95_abs_stress_error_mpa_sim_minus_real"))} |
| Max abs stress error MPa | {_fmt(real.get("max_abs_stress_error_mpa"))} | {_fmt(sim.get("max_abs_stress_error_mpa"))} | {_fmt(delta.get("max_abs_stress_error_mpa_sim_minus_real"))} |
"""


def _fmt(value: object) -> str:
    number = _number(value)
    if number is None:
        return ""
    return f"{number:.4g}"


def _write_plot(path: Path, real: list[NormalizedPoint], sim: list[NormalizedPoint]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    _plot_stress_time(axes[0][0], real, "real", "#1f77b4")
    _plot_stress_time(axes[0][0], sim, "simulation", "#d62728")
    axes[0][0].set_title("Stress vs time")
    axes[0][0].set_xlabel("normalized elapsed s")
    axes[0][0].set_ylabel("MPa")
    axes[0][0].legend(fontsize=8, loc="best")
    axes[0][0].grid(True, alpha=0.25)

    _plot_strain_current(axes[0][1], real, "real", "#1f77b4")
    _plot_strain_current(axes[0][1], sim, "simulation", "#d62728")
    axes[0][1].set_title("Strain vs current")
    axes[0][1].set_xlabel("mA")
    axes[0][1].set_ylabel("strain %")
    axes[0][1].legend(fontsize=8, loc="best")
    axes[0][1].grid(True, alpha=0.25)

    _plot_current_time(axes[1][0], real, "real", "#1f77b4")
    _plot_current_time(axes[1][0], sim, "simulation", "#d62728")
    axes[1][0].set_title("Current vs time")
    axes[1][0].set_xlabel("normalized elapsed s")
    axes[1][0].set_ylabel("mA")
    axes[1][0].legend(fontsize=8, loc="best")
    axes[1][0].grid(True, alpha=0.25)

    labels = ["real", "simulation"]
    summaries = [summarize_points(real), summarize_points(sim)]
    axes[1][1].bar(labels, [float(summary["hold_row_fraction"]) for summary in summaries], color=["#1f77b4", "#d62728"])
    axes[1][1].set_title("Current-hold row fraction")
    axes[1][1].set_ylim(bottom=0.0)
    axes[1][1].grid(True, axis="y", alpha=0.25)
    fig.suptitle("TMA real-vs-simulation comparison")
    fig.savefig(path, dpi=170)
    plt.close(fig)


def _normalized_elapsed(points: list[NormalizedPoint]) -> list[float]:
    if not points:
        return []
    start = points[0].elapsed_s
    return [point.elapsed_s - start for point in points]


def _plot_stress_time(ax: object, points: list[NormalizedPoint], label: str, color: str) -> None:
    elapsed = _normalized_elapsed(points)
    stress = [point.stress_mpa for point in points]
    ax.plot(elapsed, stress, color=color, lw=0.9, alpha=0.8, label=f"{label} stress")
    target = [point.target_mpa for point in points]
    if any(value is not None for value in target):
        ax.plot(elapsed, target, color=color, lw=0.8, ls="--", alpha=0.7, label=f"{label} target")


def _plot_strain_current(ax: object, points: list[NormalizedPoint], label: str, color: str) -> None:
    current = [point.current_ma for point in points]
    strain = [point.strain_pct for point in points]
    ax.plot(current, strain, color=color, lw=0.75, alpha=0.75, label=label)
    hold_current = [point.current_ma for point in points if point.hold_active]
    hold_strain = [point.strain_pct for point in points if point.hold_active]
    ax.scatter(hold_current, hold_strain, color=color, s=7, alpha=0.35)


def _plot_current_time(ax: object, points: list[NormalizedPoint], label: str, color: str) -> None:
    ax.plot(_normalized_elapsed(points), [point.current_ma for point in points], color=color, lw=0.9, alpha=0.8, label=label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", required=True, type=Path, help="Real TMA measurement.csv path.")
    parser.add_argument("--sim", required=True, type=Path, help="Simulator measurement.csv path.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/mini-dma-real-vs-sim"))
    parser.add_argument("--max-target-mpa", type=float, default=None, help="Keep only rows at or below this target stress.")
    parser.add_argument("--target-tolerance-mpa", type=float, default=0.0, help="Tolerance added to --max-target-mpa.")
    args = parser.parse_args(argv)
    paths = compare_measurements(
        args.real,
        args.sim,
        args.out,
        max_target_mpa=args.max_target_mpa,
        target_tolerance_mpa=args.target_tolerance_mpa,
    )
    print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
