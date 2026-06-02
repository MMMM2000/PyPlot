"""Mini DMA per-run quality metrics.

The output is a derived cache. Raw run CSV/JSON files remain the source of truth.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ANALYZER_VERSION = "2026-06-02.2"
CURRENT_PHASES = {"current", "current_hold", "current_limit_unwind"}
FAILED_STOP_REASONS = {"not_started", "failed_setup"}


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
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
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
    duration = 0.0
    if len(rows) < 2:
        return duration
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


def _metadata_mapping(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    value = metadata.get(key)
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class RunQuality:
    schema_version: int
    analyzer_version: str
    analyzed_utc: str
    run_dir: str
    run_type: str
    include_in_optimization_summary: bool
    exclusion_reasons: list[str]
    sample_name: str
    composition: str
    microwire: str
    initial_length_mm: float | None
    wire_diameter_mm: float | None
    recipe_mode: str
    recipe_summary: str
    control_logic_version: str
    control_logic_fingerprint: str
    git_branch: str
    git_commit: str
    stop_reason: str
    stop_detail: str
    measurement_rows: int
    current_loop_count_estimate: int
    total_elapsed_s: float | None
    current_phase_elapsed_s: float
    current_hold_elapsed_s: float
    current_hold_fraction: float | None
    stress_error_mean_mpa: float | None
    stress_error_median_mpa: float | None
    stress_error_mean_abs_mpa: float | None
    stress_error_median_abs_mpa: float | None
    stress_error_rms_mpa: float | None
    stress_error_p95_abs_mpa: float | None
    stress_error_max_abs_mpa: float | None
    stress_error_sample_count: int
    stress_min_mpa: float | None
    stress_max_mpa: float | None
    current_set_max_mA: float | None
    current_measured_max_mA: float | None
    current_compliance_ratio: float | None
    biggest_problems: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_current_loop_count(rows: list[dict[str, str]]) -> int:
    if len(rows) < 3:
        return 0
    current = [_float_or_none(row.get("current_set_mA") or row.get("current_measured_mA")) for row in rows]
    values = [value for value in current if value is not None]
    if len(values) < 3:
        return 0
    reversals = 0
    previous_sign = 0
    for earlier, later in zip(values[:-1], values[1:]):
        delta = later - earlier
        sign = 1 if delta > 0.05 else -1 if delta < -0.05 else 0
        if sign and previous_sign and sign != previous_sign:
            reversals += 1
        if sign:
            previous_sign = sign
    return max(0, reversals)


def classify_run(metadata: dict[str, Any], rows: list[dict[str, str]]) -> str:
    stop_reason = str(_metadata_mapping(metadata, "stop").get("reason") or "")
    if stop_reason in {"not_started", "failed_setup"}:
        return "failed_setup"
    if stop_reason == "timeout":
        return "excluded"
    recipe_summary = str(metadata.get("recipe_summary") or "").lower()
    run_name = str(metadata.get("sample_name") or "").lower()
    if "optimization" in recipe_summary or "speed" in recipe_summary or "optimization" in run_name:
        return "optimization_probe"
    if str(metadata.get("recipe_mode") or ""):
        return "normal_measurement"
    return "excluded"


def analyze_run_quality(
    run_dir: Path | str,
    *,
    min_measurement_rows: int = 100,
    min_current_loops: int = 1,
) -> RunQuality:
    run_path = Path(run_dir)
    metadata = _read_json(run_path / "metadata.json")
    rows = _read_csv_rows(run_path / "measurement.csv")
    name_fields = _metadata_mapping(metadata, "name_fields")
    stop = _metadata_mapping(metadata, "stop")
    control_logic = _metadata_mapping(metadata, "control_logic")
    source_control = _metadata_mapping(metadata, "source_control")

    elapsed_values = [value for value in (_float_or_none(row.get("elapsed_s")) for row in rows) if value is not None]
    stress_values = [value for value in (_float_or_none(row.get("stress_mpa")) for row in rows) if value is not None]
    current_set = [value for value in (_float_or_none(row.get("current_set_mA")) for row in rows) if value is not None]
    current_measured = [
        value for value in (_float_or_none(row.get("current_measured_mA")) for row in rows) if value is not None
    ]
    errors: list[float] = []
    for row in rows:
        phase = str(row.get("automation_phase") or "")
        if phase not in CURRENT_PHASES:
            continue
        stress = _float_or_none(row.get("stress_mpa"))
        target = _float_or_none(row.get("automation_target_value"))
        if stress is None or target is None:
            continue
        errors.append(stress - target)
    abs_errors = [abs(value) for value in errors]
    current_phase_elapsed = _phase_duration_s(rows, CURRENT_PHASES - {"current_hold"})
    current_hold_elapsed = _phase_duration_s(rows, {"current_hold"})
    total_elapsed = max(elapsed_values) - min(elapsed_values) if elapsed_values else None
    current_loop_count = estimate_current_loop_count(rows)
    run_type = classify_run(metadata, rows)
    exclusion_reasons: list[str] = []
    stop_reason = str(stop.get("reason") or "")
    if stop_reason in FAILED_STOP_REASONS:
        exclusion_reasons.append(f"stop_reason:{stop_reason}")
    if len(rows) < min_measurement_rows:
        exclusion_reasons.append(f"measurement_rows<{min_measurement_rows}")
    if current_loop_count < min_current_loops:
        exclusion_reasons.append(f"current_loops<{min_current_loops}")
    if not errors:
        exclusion_reasons.append("no_stress_error_samples")
    include = not exclusion_reasons and run_type != "excluded"

    biggest_problems: list[str] = []
    max_abs = max(abs_errors) if abs_errors else None
    p95_abs = _percentile(abs_errors, 0.95)
    if max_abs is not None and max_abs > 50.0:
        biggest_problems.append("large_stress_error")
    if p95_abs is not None and p95_abs > 20.0:
        biggest_problems.append("high_p95_stress_error")
    if current_set and current_measured and max(current_set) > 0:
        compliance_ratio = max(current_measured) / max(current_set)
        if compliance_ratio < 0.8:
            biggest_problems.append("current_compliance_limited")
    else:
        compliance_ratio = None
    if stop_reason and stop_reason not in {"recipe_completed", "completed"}:
        biggest_problems.append(f"stopped:{stop_reason}")
    if current_hold_elapsed == 0.0 and max_abs is not None and max_abs > 10.0:
        biggest_problems.append("no_current_hold_despite_error")

    rms = math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else None
    return RunQuality(
        schema_version=1,
        analyzer_version=ANALYZER_VERSION,
        analyzed_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        run_dir=str(run_path),
        run_type=run_type,
        include_in_optimization_summary=include,
        exclusion_reasons=exclusion_reasons,
        sample_name=str(metadata.get("sample_name") or ""),
        composition=str(name_fields.get("composition") or ""),
        microwire=str(name_fields.get("microwire") or ""),
        initial_length_mm=_float_or_none(metadata.get("initial_length_mm")),
        wire_diameter_mm=_float_or_none(metadata.get("wire_diameter_mm")),
        recipe_mode=str(metadata.get("recipe_mode") or ""),
        recipe_summary=str(metadata.get("recipe_summary") or ""),
        control_logic_version=str(control_logic.get("version") or ""),
        control_logic_fingerprint=str(control_logic.get("fingerprint") or ""),
        git_branch=str(source_control.get("branch") or ""),
        git_commit=str(source_control.get("commit") or ""),
        stop_reason=stop_reason,
        stop_detail=str(stop.get("detail") or ""),
        measurement_rows=len(rows),
        current_loop_count_estimate=current_loop_count,
        total_elapsed_s=total_elapsed,
        current_phase_elapsed_s=current_phase_elapsed,
        current_hold_elapsed_s=current_hold_elapsed,
        current_hold_fraction=(
            current_hold_elapsed / (current_hold_elapsed + current_phase_elapsed)
            if current_hold_elapsed + current_phase_elapsed > 0.0
            else None
        ),
        stress_error_mean_mpa=statistics.mean(errors) if errors else None,
        stress_error_median_mpa=statistics.median(errors) if errors else None,
        stress_error_mean_abs_mpa=statistics.mean(abs_errors) if abs_errors else None,
        stress_error_median_abs_mpa=statistics.median(abs_errors) if abs_errors else None,
        stress_error_rms_mpa=rms,
        stress_error_p95_abs_mpa=p95_abs,
        stress_error_max_abs_mpa=max_abs,
        stress_error_sample_count=len(errors),
        stress_min_mpa=min(stress_values) if stress_values else None,
        stress_max_mpa=max(stress_values) if stress_values else None,
        current_set_max_mA=max(current_set) if current_set else None,
        current_measured_max_mA=max(current_measured) if current_measured else None,
        current_compliance_ratio=compliance_ratio,
        biggest_problems=biggest_problems,
    )


def write_run_quality(quality: RunQuality, output_path: Path | str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(quality.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def analyze_and_write_run_quality(run_dir: Path | str, *, output_path: Path | str | None = None) -> RunQuality:
    quality = analyze_run_quality(run_dir)
    path = Path(output_path) if output_path is not None else Path(run_dir) / "run_quality.json"
    write_run_quality(quality, path)
    return quality


def discover_quality_run_dirs(paths: Iterable[Path | str]) -> list[Path]:
    run_dirs: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file() and path.name == "metadata.json":
            run_dirs.append(path.parent)
        elif path.is_dir() and (path / "metadata.json").exists():
            run_dirs.append(path)
        elif path.is_dir():
            run_dirs.extend(child for child in sorted(path.iterdir()) if child.is_dir() and (child / "metadata.json").exists())
    unique: list[Path] = []
    seen: set[str] = set()
    for run_dir in run_dirs:
        key = str(run_dir.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(run_dir)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Mini DMA per-run quality metrics.")
    parser.add_argument("paths", nargs="+", help="Run folders, metadata.json files, or parent folders.")
    parser.add_argument("--write", action="store_true", help="Write run_quality.json into each run folder.")
    parser.add_argument("--json", action="store_true", help="Print JSON rows instead of a compact text summary.")
    args = parser.parse_args(argv)
    run_dirs = discover_quality_run_dirs(args.paths)
    qualities = [analyze_run_quality(run_dir) for run_dir in run_dirs]
    if args.write:
        for quality in qualities:
            write_run_quality(quality, Path(quality.run_dir) / "run_quality.json")
    if args.json:
        print(json.dumps([quality.to_dict() for quality in qualities], indent=2, ensure_ascii=False))
    else:
        for quality in qualities:
            status = "include" if quality.include_in_optimization_summary else "exclude"
            print(
                f"{Path(quality.run_dir).name}: {status}, rows={quality.measurement_rows}, "
                f"loops={quality.current_loop_count_estimate}, rms={quality.stress_error_rms_mpa}, "
                f"p95={quality.stress_error_p95_abs_mpa}, problems={','.join(quality.biggest_problems)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
