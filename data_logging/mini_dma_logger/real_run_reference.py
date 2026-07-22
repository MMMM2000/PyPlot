"""Summarize real TMA runs for simulator calibration."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def _number(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _measurement_metrics(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    strain: list[float] = []
    stress: list[float] = []
    current: list[float] = []
    target: list[float] = []
    phases: dict[str, int] = {}
    hold_rows = 0
    current_rows = 0
    ramp_rows = 0
    try:
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            for row in csv.DictReader(handle):
                strain_value = _number(row.get("strain_pct"))
                if strain_value is not None:
                    strain.append(strain_value)
                stress_value = _number(row.get("stress_mpa"))
                if stress_value is not None:
                    stress.append(stress_value)
                current_value = _number(row.get("current_measured_mA") or row.get("current_set_mA"))
                if current_value is not None:
                    current.append(current_value)
                target_value = _number(row.get("automation_target_value"))
                if target_value is not None:
                    target.append(target_value)
                phase = (row.get("automation_phase") or "").strip()
                if phase:
                    phases[phase] = phases.get(phase, 0) + 1
                    hold_rows += int(phase == "current_hold")
                    current_rows += int(phase == "current")
                    ramp_rows += int(phase == "target_ramp")
    except OSError as exc:
        return {"measurement_error": str(exc)}

    phase_rows = sum(phases.values())
    return {
        "strain_min_pct": min(strain) if strain else None,
        "strain_max_pct": max(strain) if strain else None,
        "strain_span_pct": max(strain) - min(strain) if strain else None,
        "stress_min_mpa_meas": min(stress) if stress else None,
        "stress_max_mpa_meas": max(stress) if stress else None,
        "current_min_mA_meas": min(current) if current else None,
        "current_max_mA_meas_from_csv": max(current) if current else None,
        "target_min_mpa_meas": min(target) if target else None,
        "target_max_mpa_meas": max(target) if target else None,
        "current_hold_row_fraction": hold_rows / phase_rows if phase_rows else None,
        "current_row_fraction": current_rows / phase_rows if phase_rows else None,
        "target_ramp_row_fraction": ramp_rows / phase_rows if phase_rows else None,
        "measurement_phase_counts": json.dumps(phases, sort_keys=True),
    }


def summarize_run_quality(path: Path) -> dict[str, object] | None:
    """Return one compact reference row for a TMA ``run_quality.json`` file."""

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None

    run_dir = Path(str(data.get("run_dir") or path.parent))
    error_by_phase = data.get("stress_error_by_phase") or {}
    row: dict[str, object] = {
        "run_dir": str(run_dir),
        "folder": run_dir.name,
        "sample_name": data.get("sample_name", ""),
        "composition": data.get("composition", ""),
        "microwire": data.get("microwire", ""),
        "recipe_mode": data.get("recipe_mode", ""),
        "run_type": data.get("run_type", ""),
        "include_in_optimization_summary": data.get("include_in_optimization_summary"),
        "exclusion_reasons": ";".join(data.get("exclusion_reasons") or []),
        "stop_classification": data.get("stop_classification", ""),
        "stop_reason": data.get("stop_reason", ""),
        "initial_length_mm": data.get("initial_length_mm"),
        "wire_diameter_mm": data.get("wire_diameter_mm"),
        "measurement_rows": data.get("measurement_rows"),
        "control_trace_rows": data.get("control_trace_rows"),
        "total_elapsed_s": data.get("total_elapsed_s"),
        "current_phase_elapsed_s": data.get("current_phase_elapsed_s"),
        "current_hold_fraction": data.get("current_hold_fraction"),
        "current_hold_elapsed_s": data.get("current_hold_elapsed_s"),
        "stress_error_max_abs_mpa": data.get("stress_error_max_abs_mpa"),
        "stress_error_p95_abs_mpa": data.get("stress_error_p95_abs_mpa"),
        "target_ramp_max_abs_mpa": (error_by_phase.get("target_ramp") or {}).get("max_abs_mpa"),
        "current_max_abs_mpa": (error_by_phase.get("current") or {}).get("max_abs_mpa"),
        "current_hold_max_abs_mpa": (error_by_phase.get("current_hold") or {}).get("max_abs_mpa"),
        "current_set_max_mA": data.get("current_set_max_mA"),
        "current_measured_max_mA": data.get("current_measured_max_mA"),
    }
    row.update(_measurement_metrics(run_dir / "measurement.csv"))
    return row


def collect_real_run_references(root: Path) -> list[dict[str, object]]:
    """Collect real-run reference rows below ``root``."""

    rows = [row for quality in root.rglob("run_quality.json") if (row := summarize_run_quality(quality)) is not None]
    rows.sort(key=_reference_sort_key)
    return rows


def _reference_sort_key(row: dict[str, object]) -> tuple[bool, float, float]:
    included = row.get("include_in_optimization_summary") is True
    span = _number(row.get("strain_span_pct")) or -1.0
    error = _number(row.get("stress_error_p95_abs_mpa"))
    return (not included, -span, error if error is not None else 9999.0)


def write_reference_outputs(rows: list[dict[str, object]], out_dir: Path) -> dict[str, Path]:
    """Write machine-readable and human-readable real-run reference artifacts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "real_run_reference_summary.json"
    csv_path = out_dir / "real_run_reference_summary.csv"
    report_path = out_dir / "real_run_reference_summary.md"
    plot_path = out_dir / "real_run_reference_summary.png"

    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    report_path.write_text(_reference_report(rows), encoding="utf-8")
    _write_reference_plot(rows, plot_path)
    return {"json": json_path, "csv": csv_path, "report": report_path, "plot": plot_path}


def _reference_report(rows: list[dict[str, object]]) -> str:
    usable = [
        row
        for row in rows
        if row.get("include_in_optimization_summary") is True and _number(row.get("strain_span_pct")) is not None
    ]
    lines = [
        "# TMA real-run reference summary",
        "",
        f"Found {len(rows)} run_quality.json files; {len(usable)} included reference runs with measured strain.",
        "",
        "## High-strain included references",
        "",
    ]
    for row in usable[:20]:
        lines.append(
            "- {folder}: strain span {span:.2f}%, target {target_min}->{target_max} MPa, "
            "current max {current_max} mA, hold fraction {hold}, p95 stress error {p95} MPa".format(
                folder=row.get("folder"),
                span=_number(row.get("strain_span_pct")) or 0.0,
                target_min=row.get("target_min_mpa_meas"),
                target_max=row.get("target_max_mpa_meas"),
                current_max=row.get("current_measured_max_mA") or row.get("current_max_mA_meas_from_csv"),
                hold=row.get("current_hold_fraction"),
                p95=row.get("stress_error_p95_abs_mpa"),
            )
        )

    weak = sorted(
        [row for row in rows if _number(row.get("strain_span_pct")) is not None],
        key=lambda row: (_number(row.get("strain_span_pct")) or 9999.0, -(_number(row.get("stress_error_p95_abs_mpa")) or 0.0)),
    )
    lines += ["", "## Weak/noisy candidate references", ""]
    for row in weak[:20]:
        lines.append(
            "- {folder}: strain span {span:.2f}%, included={included}, stop={stop}, "
            "p95 stress error {p95} MPa".format(
                folder=row.get("folder"),
                span=_number(row.get("strain_span_pct")) or 0.0,
                included=row.get("include_in_optimization_summary"),
                stop=row.get("stop_classification"),
                p95=row.get("stress_error_p95_abs_mpa"),
            )
        )
    return "\n".join(lines) + "\n"


def _write_reference_plot(rows: list[dict[str, object]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
    span_error_points = [
        (
            _number(row.get("strain_span_pct")),
            _number(row.get("stress_error_p95_abs_mpa")),
            row.get("include_in_optimization_summary") is True,
        )
        for row in rows
    ]
    span_error_points = [(span, error, included) for span, error, included in span_error_points if span is not None and error is not None]
    axes[0].scatter(
        [point[0] for point in span_error_points],
        [point[1] for point in span_error_points],
        c=["tab:blue" if point[2] else "tab:gray" for point in span_error_points],
        alpha=0.75,
        s=28,
    )
    axes[0].set_xlabel("measured strain span (%)")
    axes[0].set_ylabel("p95 stress error (MPa)")
    axes[0].set_title("Real runs: strain span vs stress error")
    axes[0].grid(True, alpha=0.25)

    hold_points = [
        (
            _number(row.get("current_hold_fraction")),
            _number(row.get("strain_span_pct")),
            row.get("include_in_optimization_summary") is True,
        )
        for row in rows
    ]
    hold_points = [(hold, span, included) for hold, span, included in hold_points if hold is not None and span is not None]
    axes[1].scatter(
        [point[0] for point in hold_points],
        [point[1] for point in hold_points],
        c=["tab:blue" if point[2] else "tab:gray" for point in hold_points],
        alpha=0.75,
        s=28,
    )
    axes[1].set_xlabel("current-hold fraction")
    axes[1].set_ylabel("measured strain span (%)")
    axes[1].set_title("Real runs: hold time vs strain span")
    axes[1].grid(True, alpha=0.25)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Root folder containing TMA run folders.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/mini-dma-real-run-reference"))
    args = parser.parse_args(argv)

    rows = collect_real_run_references(args.root)
    paths = write_reference_outputs(rows, args.out)
    print(
        json.dumps(
            {
                "runs": len(rows),
                "included_with_strain": sum(
                    1
                    for row in rows
                    if row.get("include_in_optimization_summary") is True
                    and _number(row.get("strain_span_pct")) is not None
                ),
                "outputs": {key: str(value) for key, value in paths.items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
