"""Mini DMA per-run core plot generation."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .run_quality import RunQuality, analyze_and_write_run_quality, analyze_run_quality


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


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


def _row_xy(rows: list[dict[str, str]], x_name: str, y_name: str) -> tuple[list[float], list[float]]:
    x_values: list[float] = []
    y_values: list[float] = []
    for row in rows:
        x = _float_or_none(row.get(x_name))
        y = _float_or_none(row.get(y_name))
        if x is None or y is None:
            continue
        x_values.append(x)
        y_values.append(y)
    return x_values, y_values


def _hold_xy(rows: list[dict[str, str]]) -> tuple[list[float], list[float]]:
    x_values: list[float] = []
    y_values: list[float] = []
    for row in rows:
        if str(row.get("automation_phase") or "") != "current_hold":
            continue
        x = _float_or_none(row.get("current_measured_mA"))
        if x is None:
            x = _float_or_none(row.get("current_set_mA"))
        y = _float_or_none(row.get("strain_pct"))
        if x is None or y is None:
            continue
        x_values.append(x)
        y_values.append(y)
    return x_values, y_values


def _fmt(value: float | None, suffix: str = "", *, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def _annotation(quality: RunQuality) -> str:
    control = quality.control_logic_version or quality.control_logic_fingerprint or "n/a"
    if quality.control_logic_version and quality.control_logic_fingerprint:
        control = f"{quality.control_logic_version} / {quality.control_logic_fingerprint[:18]}"
    return "\n".join(
        [
            f"stop: {quality.stop_reason or 'n/a'}",
            f"class: {quality.stop_classification}",
            f"control: {control}",
            f"rms/p95/max: {_fmt(quality.stress_error_rms_mpa)} / "
            f"{_fmt(quality.stress_error_p95_abs_mpa)} / {_fmt(quality.stress_error_max_abs_mpa)} MPa",
            f"hold recovery max: {_fmt(quality.current_hold_recovery_time_max_s, ' s')}, "
            f"overshoot: {_fmt(quality.current_hold_overshoot_max_mpa, ' MPa')}",
            f"hold: {_fmt(quality.current_hold_fraction * 100.0 if quality.current_hold_fraction is not None else None, '%')}"
            f" of current phase",
            f"limit/compliance events: {quality.voltage_limit_event_count}/{quality.current_compliance_event_count}",
            f"max I: {_fmt(quality.current_measured_max_mA, ' mA')}",
            f"d/L: {_fmt(quality.wire_diameter_mm, ' mm', digits=4)} / "
            f"{_fmt(quality.initial_length_mm, ' mm', digits=3)}",
        ]
    )


def generate_core_run_plot(
    run_dir: Path | str,
    *,
    image_path: Path | str | None = None,
    summary_path: Path | str | None = None,
    write_quality: bool = True,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    missing = [name for name in ("measurement.csv",) if not (run_path / name).exists()]
    if missing:
        raise FileNotFoundError(f"Mini DMA run folder is missing required file(s): {', '.join(missing)} in {run_path}")
    rows = _read_csv_rows(run_path / "measurement.csv")
    quality = analyze_and_write_run_quality(run_path) if write_quality else analyze_run_quality(run_path)
    if image_path is None:
        image = run_path / "diagnostics" / "core_plots" / f"{run_path.name}_stress_time_strain_current.png"
    else:
        image = Path(image_path)
    if summary_path is None:
        summary = image.with_suffix(".json")
    else:
        summary = Path(summary_path)

    elapsed, stress = _row_xy(rows, "elapsed_s", "stress_mpa")
    measured_current, strain = _row_xy(rows, "current_measured_mA", "strain_pct")
    if not measured_current:
        measured_current, strain = _row_xy(rows, "current_set_mA", "strain_pct")
    hold_time_spans = [
        (float(window["start_s"]), float(window["end_s"]))
        for window in quality.current_hold_windows
        if window.get("start_s") is not None and window.get("end_s") is not None
    ]
    if not hold_time_spans:
        hold_time_spans = _hold_spans(rows)
    hold_current, hold_strain = _hold_xy(rows)

    image.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 6.8))
    fig.subplots_adjust(left=0.06, right=0.985, top=0.86, bottom=0.24, wspace=0.22)
    fig.patch.set_facecolor("white")

    ax = axes[0]
    ax.plot(elapsed, stress, color="#2563eb", linewidth=1.4)
    for start, end in hold_time_spans:
        ax.axvspan(start, end, color="#f59e0b", alpha=0.24)
    ax.set_title("Stress vs time")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Stress (MPa)")
    ax.grid(True, alpha=0.25)

    ax = axes[1]
    ax.plot(measured_current, strain, color="#047857", linewidth=1.4)
    if hold_current:
        ax.scatter(hold_current, hold_strain, color="#dc2626", s=12, label="current hold", zorder=3)
        ax.legend(loc="best")
    ax.set_title("Strain vs measured current")
    ax.set_xlabel("Measured current (mA)")
    ax.set_ylabel("Strain (%)")
    ax.grid(True, alpha=0.25)

    fig.suptitle(f"{run_path.name}  |  {quality.stop_reason or 'stop n/a'}", fontsize=13, fontweight="bold")
    fig.text(
        0.015,
        0.025,
        _annotation(quality),
        ha="left",
        va="bottom",
        fontsize=9,
        family="monospace",
        bbox={"facecolor": "white", "edgecolor": "#d1d5db", "boxstyle": "round,pad=0.35", "alpha": 0.95},
    )
    fig.savefig(image, dpi=170)
    plt.close(fig)

    summary_payload = {
        "run_dir": str(run_path),
        "image_path": str(image),
        "summary_path": str(summary),
        "run_quality_path": str(run_path / "run_quality.json") if write_quality else None,
        "hold_span_count": len(hold_time_spans),
        "hold_spans": [{"start_s": start, "end_s": end, "duration_s": end - start} for start, end in hold_time_spans],
        "metadata_warnings": list(quality.metadata_warnings),
        "quality": quality.to_dict(),
    }
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a phone-friendly Mini DMA per-run core plot.")
    parser.add_argument("run_dir", help="Mini DMA run folder containing measurement.csv and metadata.json.")
    parser.add_argument("--out", help="PNG output path. Defaults under the run diagnostics folder.")
    parser.add_argument("--summary", help="JSON summary output path. Defaults beside the PNG.")
    parser.add_argument("--no-write-quality", action="store_true", help="Do not update run_quality.json.")
    parser.add_argument("--json", action="store_true", help="Print the summary JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = generate_core_run_plot(
        args.run_dir,
        image_path=args.out,
        summary_path=args.summary,
        write_quality=not args.no_write_quality,
    )
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Generated Mini DMA core plot: {summary['image_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
