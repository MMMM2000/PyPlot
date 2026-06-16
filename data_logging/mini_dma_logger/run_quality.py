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

ANALYZER_VERSION = "2026-06-16.1"
CURRENT_PHASES = {"current", "current_hold", "current_limit_unwind"}
FAILED_STOP_REASONS = {"not_started", "failed_setup"}
CURRENT_LIMIT_PHASES = {"current_limit_unwind", "current_limit_return_skipped"}
VOLTAGE_LIMIT_FRACTION = 0.98
CURRENT_COMPLIANCE_RATIO = 0.8


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


def _json_warning(path: Path) -> str | None:
    if not path.exists():
        return f"missing:{path.name}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"invalid:{path.name}:{exc.__class__.__name__}"
    if not isinstance(payload, dict):
        return f"invalid:{path.name}:not_object"
    return None


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


def _elapsed(row: dict[str, str]) -> float | None:
    return _float_or_none(row.get("elapsed_s"))


def _error_value(row: dict[str, str]) -> float | None:
    stress = _float_or_none(row.get("stress_mpa"))
    target = _float_or_none(row.get("automation_target_value"))
    if stress is None or target is None:
        return None
    return stress - target


def _stats(values: list[float]) -> dict[str, float | int | None]:
    abs_values = [abs(value) for value in values]
    return {
        "count": len(values),
        "mean_mpa": statistics.mean(values) if values else None,
        "median_mpa": statistics.median(values) if values else None,
        "mean_abs_mpa": statistics.mean(abs_values) if abs_values else None,
        "median_abs_mpa": statistics.median(abs_values) if abs_values else None,
        "rms_mpa": math.sqrt(sum(value * value for value in values) / len(values)) if values else None,
        "p95_abs_mpa": _percentile(abs_values, 0.95),
        "max_abs_mpa": max(abs_values) if abs_values else None,
    }


def _stress_error_by_phase(rows: list[dict[str, str]]) -> dict[str, dict[str, float | int | None]]:
    values_by_phase: dict[str, list[float]] = {}
    for row in rows:
        phase = str(row.get("automation_phase") or "unknown") or "unknown"
        error = _error_value(row)
        if error is None:
            continue
        values_by_phase.setdefault(phase, []).append(error)
    return {phase: _stats(values) for phase, values in sorted(values_by_phase.items())}


def _current_hold_windows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    active_rows: list[dict[str, str]] = []

    def _flush() -> None:
        if not active_rows:
            return
        elapsed = [value for value in (_elapsed(row) for row in active_rows) if value is not None]
        errors = [value for value in (_error_value(row) for row in active_rows) if value is not None]
        if not elapsed:
            active_rows.clear()
            return
        start_s = elapsed[0]
        end_s = elapsed[-1]
        entry_error = errors[0] if errors else None
        exit_error = errors[-1] if errors else None
        max_abs_error = max((abs(value) for value in errors), default=None)
        target_values = [
            value
            for value in (_float_or_none(row.get("automation_target_value")) for row in active_rows)
            if value is not None
        ]
        tolerance_values = [
            value
            for value in (_float_or_none(row.get("tolerance_mpa")) for row in active_rows)
            if value is not None and value >= 0.0
        ]
        # Saved measurement rows usually do not carry the controller tolerance.
        # Use a conservative data-derived recovery band so old runs still get a
        # comparable "time to near-target again" metric.
        if tolerance_values:
            recovery_band = max(tolerance_values)
        elif entry_error is not None:
            recovery_band = max(1.0, abs(entry_error) * 0.25)
        else:
            recovery_band = None
        recovered_after_s = None
        if recovery_band is not None:
            for row in active_rows:
                elapsed_s = _elapsed(row)
                error = _error_value(row)
                if elapsed_s is None or error is None:
                    continue
                if abs(error) <= recovery_band:
                    recovered_after_s = elapsed_s - start_s
                    break
        overshoot_mpa = None
        if entry_error is not None and errors:
            entry_sign = 1 if entry_error > 0 else -1 if entry_error < 0 else 0
            if entry_sign:
                opposite = [-entry_sign * value for value in errors if value * entry_sign < 0]
                overshoot_mpa = max(opposite) if opposite else 0.0
            else:
                overshoot_mpa = 0.0
        windows.append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": end_s - start_s,
                "sample_count": len(active_rows),
                "target_mpa": statistics.median(target_values) if target_values else None,
                "entry_error_mpa": entry_error,
                "exit_error_mpa": exit_error,
                "max_abs_error_mpa": max_abs_error,
                "recovery_band_mpa": recovery_band,
                "recovered_after_s": recovered_after_s,
                "overshoot_mpa": overshoot_mpa,
            }
        )
        active_rows.clear()

    for row in rows:
        if str(row.get("automation_phase") or "") == "current_hold":
            active_rows.append(row)
        else:
            _flush()
    _flush()
    return windows


def _metadata_mapping(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    value = metadata.get(key)
    return value if isinstance(value, dict) else {}


def _classify_stop(stop_reason: str, stop_category: str) -> str:
    reason = str(stop_reason or "")
    category = str(stop_category or "")
    if reason in {"recipe_completed", "completed"}:
        return "completed"
    if reason == "wire_break_or_contact_loss":
        return "fault_wire_break_or_contact_loss"
    if reason == "mechanical_load_loss":
        return "fault_mechanical_load_loss"
    if reason in {"not_started", "failed_setup"}:
        return "failed_setup"
    if "timeout" in reason:
        return "timeout"
    if category == "operator" or reason.startswith("manual_") or reason in {"emergency_stop", "app_closed"}:
        return "operator_stop"
    if category == "fault" or reason:
        return f"fault:{reason}"
    return "unknown"


def _limit_events(
    rows: list[dict[str, str]],
    trace_rows: list[dict[str, str]],
    metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
    heating = _metadata_mapping(metadata, "heating")
    voltage_limit_v = _float_or_none(heating.get("voltage_limit_v"))
    events: list[dict[str, Any]] = []
    measurement_limit_count = 0
    compliance_count = 0
    active: dict[str, Any] | None = None

    def _finish() -> None:
        nonlocal active
        if active is not None:
            events.append(active)
            active = None

    for row in rows:
        elapsed_s = _elapsed(row)
        voltage_v = _float_or_none(row.get("voltage_V"))
        set_mA = _float_or_none(row.get("current_set_mA"))
        measured_mA = _float_or_none(row.get("current_measured_mA"))
        ratio = None
        if set_mA is not None and abs(set_mA) > 0.0 and measured_mA is not None:
            ratio = abs(measured_mA) / abs(set_mA)
        at_voltage_limit = (
            voltage_limit_v is not None
            and voltage_limit_v > 0.0
            and voltage_v is not None
            and voltage_v >= voltage_limit_v * VOLTAGE_LIMIT_FRACTION
        )
        under_current = ratio is not None and ratio < CURRENT_COMPLIANCE_RATIO
        if at_voltage_limit:
            measurement_limit_count += 1
        if under_current:
            compliance_count += 1
        if not (at_voltage_limit or under_current):
            _finish()
            continue
        if active is None:
            active = {
                "source": "measurement",
                "start_s": elapsed_s,
                "end_s": elapsed_s,
                "sample_count": 0,
                "voltage_limit_v": voltage_limit_v,
                "max_voltage_v": voltage_v,
                "min_current_ratio": ratio,
                "at_voltage_limit": False,
                "current_compliance_limited": False,
            }
        active["end_s"] = elapsed_s
        active["sample_count"] += 1
        active["at_voltage_limit"] = bool(active["at_voltage_limit"] or at_voltage_limit)
        active["current_compliance_limited"] = bool(active["current_compliance_limited"] or under_current)
        if voltage_v is not None:
            prior = active.get("max_voltage_v")
            active["max_voltage_v"] = voltage_v if prior is None else max(prior, voltage_v)
        if ratio is not None:
            prior = active.get("min_current_ratio")
            active["min_current_ratio"] = ratio if prior is None else min(prior, ratio)
    _finish()

    for row in trace_rows:
        phase = str(row.get("automation_phase") or "")
        text = " ".join(str(row.get(key) or "") for key in ("decision", "result", "reason")).lower()
        if phase not in CURRENT_LIMIT_PHASES and "voltage_limit" not in text and "current_limit" not in text:
            continue
        events.append(
            {
                "source": "control_trace",
                "elapsed_s": _elapsed(row),
                "phase": phase,
                "decision": str(row.get("decision") or ""),
                "result": str(row.get("result") or ""),
                "reason": str(row.get("reason") or ""),
            }
        )
    return events, measurement_limit_count, compliance_count


def _metadata_warnings(
    run_path: Path,
    metadata: dict[str, Any],
    rows: list[dict[str, str]],
    trace_rows: list[dict[str, str]],
    ir_rows: list[dict[str, str]],
) -> list[str]:
    warnings: list[str] = []
    metadata_warning = _json_warning(run_path / "metadata.json")
    if metadata_warning is not None:
        warnings.append(metadata_warning)
    for filename, present_rows in (
        ("measurement.csv", rows),
        ("control_trace.csv", trace_rows),
        ("ir_temperature.csv", ir_rows),
    ):
        path = run_path / filename
        if not path.exists():
            warnings.append(f"missing:{filename}")
        elif filename != "ir_temperature.csv" and not present_rows:
            warnings.append(f"empty:{filename}")
    name_fields = _metadata_mapping(metadata, "name_fields")
    stop = _metadata_mapping(metadata, "stop")
    control_logic = _metadata_mapping(metadata, "control_logic")
    source_control = _metadata_mapping(metadata, "source_control")
    if not metadata.get("sample_name"):
        warnings.append("metadata:missing_sample_name")
    if not name_fields.get("composition"):
        warnings.append("metadata:missing_composition")
    if not name_fields.get("microwire"):
        warnings.append("metadata:missing_microwire")
    diameter = _float_or_none(metadata.get("wire_diameter_mm"))
    if diameter is None or diameter <= 0.0:
        warnings.append("metadata:invalid_wire_diameter_mm")
    length = _float_or_none(metadata.get("initial_length_mm"))
    if length is None or length <= 0.0:
        warnings.append("metadata:invalid_initial_length_mm")
    if not stop.get("reason"):
        warnings.append("metadata:missing_stop_reason")
    if not control_logic.get("fingerprint"):
        warnings.append("metadata:missing_control_logic_fingerprint")
    if not source_control.get("commit"):
        warnings.append("metadata:missing_source_control_commit")
    return warnings


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
    control_logic_profile: str
    control_logic_features: list[str]
    git_branch: str
    git_commit: str
    stop_reason: str
    stop_category: str
    stop_label: str
    stop_classification: str
    stop_detail: str
    measurement_rows: int
    control_trace_rows: int
    ir_temperature_rows: int
    current_loop_count_estimate: int
    total_elapsed_s: float | None
    current_phase_elapsed_s: float
    current_hold_elapsed_s: float
    current_hold_fraction: float | None
    stress_error_by_phase: dict[str, dict[str, float | int | None]]
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
    voltage_limit_event_count: int
    current_compliance_event_count: int
    voltage_current_limit_events: list[dict[str, Any]]
    current_hold_windows: list[dict[str, Any]]
    current_hold_window_count: int
    current_hold_recovered_count: int
    current_hold_recovery_time_median_s: float | None
    current_hold_recovery_time_max_s: float | None
    current_hold_overshoot_max_mpa: float | None
    metadata_warnings: list[str]
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
    trace_rows = _read_csv_rows(run_path / "control_trace.csv")
    ir_rows = _read_csv_rows(run_path / "ir_temperature.csv")
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
        error = _error_value(row)
        if error is None:
            continue
        errors.append(error)
    abs_errors = [abs(value) for value in errors]
    current_phase_elapsed = _phase_duration_s(rows, CURRENT_PHASES - {"current_hold"})
    current_hold_elapsed = _phase_duration_s(rows, {"current_hold"})
    total_elapsed = max(elapsed_values) - min(elapsed_values) if elapsed_values else None
    current_loop_count = estimate_current_loop_count(rows)
    run_type = classify_run(metadata, rows)
    stress_error_by_phase = _stress_error_by_phase(rows)
    current_hold_windows = _current_hold_windows(rows)
    recovered_times = [
        float(window["recovered_after_s"])
        for window in current_hold_windows
        if window.get("recovered_after_s") is not None
    ]
    overshoots = [
        float(window["overshoot_mpa"])
        for window in current_hold_windows
        if window.get("overshoot_mpa") is not None
    ]
    limit_events, voltage_limit_event_count, current_compliance_event_count = _limit_events(rows, trace_rows, metadata)
    stop_reason = str(stop.get("reason") or "")
    stop_category = str(stop.get("category") or "")
    stop_classification = _classify_stop(stop_reason, stop_category)
    metadata_warnings = _metadata_warnings(run_path, metadata, rows, trace_rows, ir_rows)
    exclusion_reasons: list[str] = []
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
    if voltage_limit_event_count:
        biggest_problems.append("voltage_limit_events")
    if current_compliance_event_count:
        biggest_problems.append("current_compliance_events")
    if metadata_warnings:
        biggest_problems.append("metadata_warnings")

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
        control_logic_profile=str(control_logic.get("profile") or ""),
        control_logic_features=[
            str(feature)
            for feature in control_logic.get("features", [])
            if isinstance(control_logic.get("features"), list)
        ],
        git_branch=str(source_control.get("branch") or ""),
        git_commit=str(source_control.get("commit") or ""),
        stop_reason=stop_reason,
        stop_category=stop_category,
        stop_label=str(stop.get("label") or ""),
        stop_classification=stop_classification,
        stop_detail=str(stop.get("detail") or ""),
        measurement_rows=len(rows),
        control_trace_rows=len(trace_rows),
        ir_temperature_rows=len(ir_rows),
        current_loop_count_estimate=current_loop_count,
        total_elapsed_s=total_elapsed,
        current_phase_elapsed_s=current_phase_elapsed,
        current_hold_elapsed_s=current_hold_elapsed,
        current_hold_fraction=(
            current_hold_elapsed / (current_hold_elapsed + current_phase_elapsed)
            if current_hold_elapsed + current_phase_elapsed > 0.0
            else None
        ),
        stress_error_by_phase=stress_error_by_phase,
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
        voltage_limit_event_count=voltage_limit_event_count,
        current_compliance_event_count=current_compliance_event_count,
        voltage_current_limit_events=limit_events,
        current_hold_windows=current_hold_windows,
        current_hold_window_count=len(current_hold_windows),
        current_hold_recovered_count=len(recovered_times),
        current_hold_recovery_time_median_s=statistics.median(recovered_times) if recovered_times else None,
        current_hold_recovery_time_max_s=max(recovered_times) if recovered_times else None,
        current_hold_overshoot_max_mpa=max(overshoots) if overshoots else None,
        metadata_warnings=metadata_warnings,
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
        elif path.is_file() and path.name == "measurement.csv":
            run_dirs.append(path.parent)
        elif path.is_dir() and ((path / "metadata.json").exists() or (path / "measurement.csv").exists()):
            run_dirs.append(path)
        elif path.is_dir():
            run_dirs.extend(
                child
                for child in sorted(path.iterdir())
                if child.is_dir() and ((child / "metadata.json").exists() or (child / "measurement.csv").exists())
            )
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
    parser.add_argument(
        "--core-plots",
        action="store_true",
        help="Also generate the standard phone-friendly core PNG and JSON summary for each run.",
    )
    parser.add_argument(
        "--core-plot-dir",
        help="Optional folder for generated core PNG/JSON files. Defaults under each run diagnostics folder.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON rows instead of a compact text summary.")
    args = parser.parse_args(argv)
    run_dirs = discover_quality_run_dirs(args.paths)
    qualities = [analyze_run_quality(run_dir) for run_dir in run_dirs]
    if args.write or args.core_plots:
        for quality in qualities:
            write_run_quality(quality, Path(quality.run_dir) / "run_quality.json")
    plot_summaries: list[dict[str, Any]] = []
    if args.core_plots:
        from .run_core_plot import generate_core_run_plot

        for quality in qualities:
            run_path = Path(quality.run_dir)
            image_path = None
            summary_path = None
            if args.core_plot_dir:
                output_dir = Path(args.core_plot_dir)
                image_path = output_dir / f"{run_path.name}_stress_time_strain_current.png"
                summary_path = image_path.with_suffix(".json")
            plot_summaries.append(
                generate_core_run_plot(
                    run_path,
                    image_path=image_path,
                    summary_path=summary_path,
                    write_quality=True,
                )
            )
    if args.json:
        payload: Any
        if args.core_plots:
            payload = [
                {
                    **quality.to_dict(),
                    "core_plot_path": plot_summary["image_path"],
                    "core_plot_summary_path": plot_summary["summary_path"],
                }
                for quality, plot_summary in zip(qualities, plot_summaries)
            ]
        else:
            payload = [quality.to_dict() for quality in qualities]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for index, quality in enumerate(qualities):
            status = "include" if quality.include_in_optimization_summary else "exclude"
            plot_note = ""
            if args.core_plots and index < len(plot_summaries):
                plot_note = f", plot={plot_summaries[index]['image_path']}"
            print(
                f"{Path(quality.run_dir).name}: {status}, rows={quality.measurement_rows}, "
                f"loops={quality.current_loop_count_estimate}, rms={quality.stress_error_rms_mpa}, "
                f"p95={quality.stress_error_p95_abs_mpa}, problems={','.join(quality.biggest_problems)}"
                f"{plot_note}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
