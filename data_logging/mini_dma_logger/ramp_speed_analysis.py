"""Compare Mini DMA current-sweep ramp speeds from saved run folders."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .trace_replay import TraceReplayResult, analyze_control_trace


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
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _phase_duration_s(rows: list[dict[str, str]], phases: set[str]) -> float:
    if len(rows) < 2:
        return 0.0
    duration = 0.0
    for previous, current in zip(rows[:-1], rows[1:]):
        phase = str(previous.get("automation_phase") or "")
        if phase not in phases:
            continue
        start = _float_or_none(previous.get("elapsed_s"))
        end = _float_or_none(current.get("elapsed_s"))
        if start is None or end is None or end < start:
            continue
        duration += end - start
    return duration


@dataclass(frozen=True)
class RampSpeedRunMetrics:
    run_dir: str
    sample_name: str
    ramp_speed_mA_s: float | None
    target_mpa: float | None
    current_start_mA: float | None
    current_end_mA: float | None
    max_measured_current_mA: float | None
    total_elapsed_s: float | None
    current_phase_elapsed_s: float
    current_hold_elapsed_s: float
    current_hold_sample_count: int
    stress_error_mean_abs_mpa: float | None
    stress_error_rms_mpa: float | None
    stress_error_p95_abs_mpa: float | None
    stress_error_max_abs_mpa: float | None
    stress_error_sample_count: int
    step_floor_only_accept_count: int | None
    max_step_floor_only_error_mpa: float | None
    stop_reason: str
    stop_detail: str


def analyze_ramp_speed_run(run_dir: Path | str) -> RampSpeedRunMetrics:
    run_path = Path(run_dir)
    metadata = _read_json(run_path / "metadata.json")
    sweep = metadata.get("controlled_current_sweep")
    if not isinstance(sweep, dict):
        sweep = {}
    stop = metadata.get("stop")
    if not isinstance(stop, dict):
        stop = {}
    rows = _read_csv_rows(run_path / "measurement.csv")

    target_mpa = _float_or_none(sweep.get("target_start"))
    if target_mpa is None:
        targets = [
            value
            for value in (_float_or_none(row.get("automation_target_value")) for row in rows)
            if value is not None
        ]
        target_mpa = statistics.median(targets) if targets else None

    current_values = [
        value
        for value in (_float_or_none(row.get("current_measured_mA")) for row in rows)
        if value is not None
    ]
    elapsed_values = [
        value
        for value in (_float_or_none(row.get("elapsed_s")) for row in rows)
        if value is not None
    ]
    stress_errors: list[float] = []
    for row in rows:
        phase = str(row.get("automation_phase") or "")
        if phase not in {"current", "current_hold", "current_limit_unwind"}:
            continue
        stress = _float_or_none(row.get("stress_mpa"))
        target = _float_or_none(row.get("automation_target_value"))
        if stress is None:
            continue
        if target is None:
            target = target_mpa
        if target is None:
            continue
        stress_errors.append(float(stress) - float(target))

    replay: TraceReplayResult | None = None
    try:
        replay = analyze_control_trace(run_path)
    except Exception:
        replay = None

    abs_errors = [abs(value) for value in stress_errors]
    rms = None
    if stress_errors:
        rms = math.sqrt(sum(value * value for value in stress_errors) / len(stress_errors))
    return RampSpeedRunMetrics(
        run_dir=str(run_path),
        sample_name=str(metadata.get("sample_name") or ""),
        ramp_speed_mA_s=_float_or_none(sweep.get("current_ramp_rate_mA_s")),
        target_mpa=target_mpa,
        current_start_mA=_float_or_none(sweep.get("current_start_mA")),
        current_end_mA=_float_or_none(sweep.get("current_end_mA")),
        max_measured_current_mA=max(current_values) if current_values else None,
        total_elapsed_s=(max(elapsed_values) - min(elapsed_values)) if elapsed_values else None,
        current_phase_elapsed_s=_phase_duration_s(rows, {"current", "current_limit_unwind"}),
        current_hold_elapsed_s=_phase_duration_s(rows, {"current_hold"}),
        current_hold_sample_count=sum(1 for row in rows if str(row.get("automation_phase") or "") == "current_hold"),
        stress_error_mean_abs_mpa=statistics.mean(abs_errors) if abs_errors else None,
        stress_error_rms_mpa=rms,
        stress_error_p95_abs_mpa=_percentile(abs_errors, 0.95),
        stress_error_max_abs_mpa=max(abs_errors) if abs_errors else None,
        stress_error_sample_count=len(stress_errors),
        step_floor_only_accept_count=None if replay is None else replay.summary.step_floor_only_accept_count,
        max_step_floor_only_error_mpa=None if replay is None else replay.summary.max_step_floor_only_error,
        stop_reason=str(stop.get("reason") or ""),
        stop_detail=str(stop.get("detail") or ""),
    )


def discover_run_dirs(paths: Iterable[Path | str]) -> list[Path]:
    run_dirs: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file() and path.name.lower().endswith(".json"):
            payload = _read_json(path)
            runs = payload.get("runs")
            if isinstance(runs, list):
                for run in runs:
                    if not isinstance(run, dict):
                        continue
                    metadata_path = run.get("metadata_path")
                    if isinstance(metadata_path, str) and metadata_path:
                        run_dirs.append(Path(metadata_path).expanduser().resolve().parent)
            continue
        if path.is_file() and path.name == "metadata.json":
            run_dirs.append(path.expanduser().resolve().parent)
            continue
        if (path / "metadata.json").exists():
            run_dirs.append(path.expanduser().resolve())
            continue
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_dir() and (child / "metadata.json").exists():
                    run_dirs.append(child.resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for run_dir in run_dirs:
        key = str(run_dir).lower()
        if key not in seen:
            seen.add(key)
            unique.append(run_dir)
    return unique


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def write_ramp_speed_outputs(metrics: list[RampSpeedRunMetrics], output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "ramp_speed_comparison.json"
    csv_path = out / "ramp_speed_comparison.csv"
    md_path = out / "ramp_speed_comparison.md"
    rows = [asdict(item) for item in metrics]
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fieldnames = list(rows[0].keys()) if rows else [field.name for field in RampSpeedRunMetrics.__dataclass_fields__.values()]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    columns = [
        "ramp_speed_mA_s",
        "stress_error_p95_abs_mpa",
        "stress_error_rms_mpa",
        "stress_error_max_abs_mpa",
        "current_hold_elapsed_s",
        "total_elapsed_s",
        "max_measured_current_mA",
        "stop_reason",
    ]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_format_value(getattr(item, column)) for column in columns) + " |"
        for item in sorted(metrics, key=lambda item: (item.ramp_speed_mA_s is None, item.ramp_speed_mA_s or 0.0))
    ]
    md_path.write_text(
        "# Mini DMA Ramp-Speed Comparison\n\n"
        + "\n".join([header, divider, *body])
        + "\n",
        encoding="utf-8",
    )
    return {"json": json_path, "csv": csv_path, "markdown": md_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Mini DMA current-sweep ramp speeds from saved run folders.")
    parser.add_argument("paths", nargs="+", help="Run folders, metadata.json files, parent folders, or bench summary JSON files.")
    parser.add_argument("--out", type=Path, default=None, help="Directory for CSV/JSON/Markdown comparison outputs.")
    args = parser.parse_args(argv)
    run_dirs = discover_run_dirs(args.paths)
    metrics = [analyze_ramp_speed_run(run_dir) for run_dir in run_dirs]
    print(json.dumps([asdict(item) for item in metrics], indent=2))
    if args.out is not None:
        outputs = write_ramp_speed_outputs(metrics, args.out)
        for label, path in outputs.items():
            print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
