"""Replay saved Mini DMA runs through the predictive controller prototype."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

from .predictive_controller import (
    PredictiveAdvice,
    PredictiveControllerTuning,
    PredictiveFeatures,
    PredictivePhase,
    PredictiveSnapshot,
    advise_predictive_control,
    estimate_predictive_features,
    select_predictive_motor_step,
)
from .ramp_speed_analysis import RampSpeedRunMetrics, analyze_ramp_speed_run
from .run_quality import RunQuality, analyze_run_quality, discover_quality_run_dirs
from .trace_replay import TraceReplayResult, analyze_control_trace


CURRENT_PHASES = {"current", "current_hold", "current_limit_unwind"}


@dataclass(frozen=True)
class PhaseReplayMetrics:
    phase: str
    sample_count: int
    elapsed_s: float
    stress_error_rms_mpa: float | None
    stress_error_p95_abs_mpa: float | None
    stress_error_max_abs_mpa: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PredictiveRunReplay:
    run_dir: str
    run_name: str
    control_logic_version: str | None
    ramp_speed_mA_s: float | None
    total_elapsed_s: float | None
    current_phase_elapsed_s: float
    current_hold_elapsed_s: float
    stress_error_rms_mpa: float | None
    stress_error_p95_abs_mpa: float | None
    stress_error_max_abs_mpa: float | None
    candidate_hold_sample_fraction: float
    candidate_hold_transition_count: int
    candidate_min_ramp_scale: float
    candidate_mean_ramp_scale: float
    candidate_estimated_extra_ramp_time_s: float
    high_risk_sample_count: int
    high_risk_covered_fraction: float | None
    stable_false_slow_fraction: float | None
    transformation_elapsed_s: float
    recovery_elapsed_s: float
    voltage_compliance_sample_count: int
    strain_current_curve_quality: str
    mean_candidate_correction_mm: float | None
    max_candidate_correction_mm: float | None
    replay_step_floor_accept_count: int | None
    phase_metrics: list[PhaseReplayMetrics]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["phase_metrics"] = [metric.to_dict() for metric in self.phase_metrics]
        return row


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _percentile(values: list[float], fraction: float) -> float | None:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return None
    if len(finite) == 1:
        return finite[0]
    position = max(0.0, min(1.0, fraction)) * (len(finite) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return finite[lower]
    weight = position - lower
    return finite[lower] * (1.0 - weight) + finite[upper] * weight


def _phase_duration(rows: list[tuple[PredictiveSnapshot, PredictiveFeatures, PredictiveAdvice]]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for index, (_, _features, advice) in enumerate(rows):
        if index + 1 >= len(rows):
            continue
        dt_s = max(0.0, rows[index + 1][0].elapsed_s - rows[index][0].elapsed_s)
        if dt_s > 5.0:
            dt_s = 0.0
        durations[advice.phase.value] = durations.get(advice.phase.value, 0.0) + dt_s
    return durations


def _phase_metrics(
    rows: list[tuple[PredictiveSnapshot, PredictiveFeatures, PredictiveAdvice]]
) -> list[PhaseReplayMetrics]:
    durations = _phase_duration(rows)
    grouped: dict[str, list[float]] = {}
    for _snapshot, features, advice in rows:
        grouped.setdefault(advice.phase.value, []).append(float(features.stress_error_mpa))
    metrics: list[PhaseReplayMetrics] = []
    for phase in sorted(grouped):
        errors = grouped[phase]
        abs_errors = [abs(value) for value in errors]
        rms = math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else None
        metrics.append(
            PhaseReplayMetrics(
                phase=phase,
                sample_count=len(errors),
                elapsed_s=durations.get(phase, 0.0),
                stress_error_rms_mpa=rms,
                stress_error_p95_abs_mpa=_percentile(abs_errors, 0.95),
                stress_error_max_abs_mpa=max(abs_errors) if abs_errors else None,
            )
        )
    return metrics


def _metadata_voltage_limit(metadata: dict[str, Any]) -> float | None:
    heating = metadata.get("heating")
    if isinstance(heating, dict):
        return _float_or_none(heating.get("voltage_limit_v"))
    return None


def _snapshots_from_measurement(
    run_dir: Path,
    metadata: dict[str, Any],
) -> list[PredictiveSnapshot]:
    rows = _read_csv_rows(run_dir / "measurement.csv")
    voltage_limit = _metadata_voltage_limit(metadata)
    snapshots: list[PredictiveSnapshot] = []
    for row in rows:
        phase = str(row.get("automation_phase") or "")
        if phase not in CURRENT_PHASES:
            continue
        basis = str(row.get("automation_basis") or "")
        if basis and basis != "stress_mpa":
            continue
        elapsed = _float_or_none(row.get("elapsed_s"))
        stress = _float_or_none(row.get("stress_mpa"))
        target = _float_or_none(row.get("automation_target_value"))
        if elapsed is None or stress is None or target is None:
            continue
        current = _float_or_none(row.get("current_measured_mA"))
        if current is None:
            current = _float_or_none(row.get("current_set_mA"))
        snapshots.append(
            PredictiveSnapshot(
                elapsed_s=elapsed,
                stress_mpa=stress,
                target_mpa=target,
                current_mA=current,
                strain_pct=_float_or_none(row.get("strain_pct")),
                voltage_V=_float_or_none(row.get("voltage_V")),
                voltage_limit_V=voltage_limit,
                automation_phase=phase,
            )
        )
    return sorted(snapshots, key=lambda item: item.elapsed_s)


def _advice_rows(
    snapshots: list[PredictiveSnapshot],
    tuning: PredictiveControllerTuning,
    *,
    window_s: float,
) -> list[tuple[PredictiveSnapshot, PredictiveFeatures, PredictiveAdvice]]:
    rows: list[tuple[PredictiveSnapshot, PredictiveFeatures, PredictiveAdvice]] = []
    history: list[PredictiveSnapshot] = []
    for snapshot in snapshots:
        history.append(snapshot)
        features = estimate_predictive_features(history, window_s=window_s)
        if features is None:
            continue
        advice = advise_predictive_control(features, tuning)
        rows.append((snapshot, features, advice))
    return rows


def _high_risk(features: PredictiveFeatures, advice: PredictiveAdvice, tuning: PredictiveControllerTuning) -> bool:
    pause_band = tuning.target_tolerance_mpa * tuning.hold_pause_factor
    if abs(features.stress_error_mpa) >= tuning.large_error_mpa:
        return True
    if advice.moving_away and abs(advice.predicted_error_mpa) >= max(pause_band, tuning.large_error_mpa * 0.5):
        return True
    return advice.phase in {PredictivePhase.TRANSFORMATION, PredictivePhase.CURRENT_LIMITED}


def _strain_curve_quality(snapshots: list[PredictiveSnapshot]) -> str:
    usable = [
        snapshot
        for snapshot in snapshots
        if snapshot.current_mA is not None and snapshot.strain_pct is not None
    ]
    if len(usable) < 25:
        return "too_few_points"
    currents = [float(snapshot.current_mA) for snapshot in usable]
    strains = [float(snapshot.strain_pct) for snapshot in usable]
    current_span = max(currents) - min(currents)
    strain_span = max(strains) - min(strains)
    if current_span < 10.0:
        return "short_current_span"
    if strain_span < 0.05:
        return "low_strain_span"
    direction_changes = 0
    previous_direction = 0.0
    for left, right in zip(currents[:-1], currents[1:], strict=False):
        delta = right - left
        if abs(delta) < 1e-9:
            continue
        direction = math.copysign(1.0, delta)
        if previous_direction and direction != previous_direction:
            direction_changes += 1
        previous_direction = direction
    if direction_changes > 4:
        return "fragmented_current_path"
    return "usable"


def _candidate_corrections(
    run_dir: Path,
    tuning: PredictiveControllerTuning,
) -> tuple[float | None, float | None]:
    rows = _read_csv_rows(run_dir / "control_trace.csv")
    corrections: list[float] = []
    previous_direction = 0.0
    for row in rows:
        if str(row.get("decision") or "") != "correction":
            continue
        basis = str(row.get("automation_basis") or row.get("basis") or "")
        if basis != "stress_mpa":
            continue
        error_value = _float_or_none(row.get("error_value"))
        sensitivity = _float_or_none(row.get("sensitivity_per_mm"))
        if error_value is None or sensitivity is None:
            continue
        phase = (
            PredictivePhase.TRANSFORMATION
            if abs(float(error_value)) >= tuning.large_error_mpa
            else PredictivePhase.STABLE_ELASTIC
        )
        advice = select_predictive_motor_step(
            stress_error_mpa=-float(error_value),
            sensitivity_mpa_per_mm=float(sensitivity),
            phase=phase,
            previous_target_space_direction=previous_direction,
            tuning=tuning,
        )
        if advice.correction_mm > 0.0:
            previous_direction = advice.target_space_direction
            corrections.append(float(advice.correction_mm))
    if not corrections:
        return None, None
    return statistics.mean(corrections), max(corrections)


def analyze_predictive_run(
    run_dir: Path | str,
    tuning: PredictiveControllerTuning | None = None,
    *,
    window_s: float = 2.5,
) -> PredictiveRunReplay:
    tuning = tuning or PredictiveControllerTuning()
    path = Path(run_dir)
    metadata = _read_json(path / "metadata.json")
    quality: RunQuality = analyze_run_quality(path)
    ramp: RampSpeedRunMetrics = analyze_ramp_speed_run(path)
    trace: TraceReplayResult | None
    try:
        trace = analyze_control_trace(path)
    except Exception:
        trace = None
    snapshots = _snapshots_from_measurement(path, metadata)
    rows = _advice_rows(snapshots, tuning, window_s=window_s)
    hold_samples = sum(1 for _snapshot, _features, advice in rows if advice.hold_current)
    hold_transitions = 0
    previous_hold = False
    for _snapshot, _features, advice in rows:
        if advice.hold_current and not previous_hold:
            hold_transitions += 1
        previous_hold = advice.hold_current
    ramp_scales = [float(advice.ramp_scale) for _snapshot, _features, advice in rows]
    extra_time = 0.0
    high_risk = 0
    high_risk_covered = 0
    stable = 0
    stable_slow = 0
    voltage_compliance = 0
    for index, (_snapshot, features, advice) in enumerate(rows):
        dt_s = 0.0
        if index + 1 < len(rows):
            dt_s = max(0.0, rows[index + 1][0].elapsed_s - rows[index][0].elapsed_s)
            if dt_s > 5.0:
                dt_s = 0.0
        if advice.ramp_scale > 0.0:
            extra_time += dt_s * (1.0 / advice.ramp_scale - 1.0)
        is_high_risk = _high_risk(features, advice, tuning)
        if is_high_risk:
            high_risk += 1
            if advice.hold_current or advice.ramp_scale <= tuning.recovery_ramp_scale:
                high_risk_covered += 1
        if advice.phase == PredictivePhase.STABLE_ELASTIC:
            stable += 1
            if advice.ramp_scale < 0.8:
                stable_slow += 1
        if (
            features.voltage_ratio is not None
            and features.voltage_ratio >= tuning.current_limit_voltage_ratio
        ):
            voltage_compliance += 1
    phase_durations = _phase_duration(rows)
    mean_correction, max_correction = _candidate_corrections(path, tuning)
    control_logic = metadata.get("control_logic") if isinstance(metadata.get("control_logic"), dict) else {}
    return PredictiveRunReplay(
        run_dir=str(path),
        run_name=path.name,
        control_logic_version=control_logic.get("version") if isinstance(control_logic, dict) else None,
        ramp_speed_mA_s=ramp.ramp_speed_mA_s,
        total_elapsed_s=quality.total_elapsed_s,
        current_phase_elapsed_s=quality.current_phase_elapsed_s,
        current_hold_elapsed_s=quality.current_hold_elapsed_s,
        stress_error_rms_mpa=quality.stress_error_rms_mpa,
        stress_error_p95_abs_mpa=quality.stress_error_p95_abs_mpa,
        stress_error_max_abs_mpa=quality.stress_error_max_abs_mpa,
        candidate_hold_sample_fraction=(hold_samples / len(rows) if rows else 0.0),
        candidate_hold_transition_count=hold_transitions,
        candidate_min_ramp_scale=min(ramp_scales) if ramp_scales else 1.0,
        candidate_mean_ramp_scale=statistics.mean(ramp_scales) if ramp_scales else 1.0,
        candidate_estimated_extra_ramp_time_s=extra_time,
        high_risk_sample_count=high_risk,
        high_risk_covered_fraction=(high_risk_covered / high_risk if high_risk else None),
        stable_false_slow_fraction=(stable_slow / stable if stable else None),
        transformation_elapsed_s=phase_durations.get(PredictivePhase.TRANSFORMATION.value, 0.0),
        recovery_elapsed_s=phase_durations.get(PredictivePhase.RECOVERY.value, 0.0),
        voltage_compliance_sample_count=voltage_compliance,
        strain_current_curve_quality=_strain_curve_quality(snapshots),
        mean_candidate_correction_mm=mean_correction,
        max_candidate_correction_mm=max_correction,
        replay_step_floor_accept_count=(
            None if trace is None else trace.summary.step_floor_only_accept_count
        ),
        phase_metrics=_phase_metrics(rows),
    )


def write_predictive_replay_outputs(
    results: Iterable[PredictiveRunReplay],
    output_dir: Path | str,
) -> dict[str, Path]:
    rows = list(results)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "predictive_replay_summary.json"
    csv_path = out / "predictive_replay_summary.csv"
    md_path = out / "predictive_replay_summary.md"
    json_path.write_text(
        json.dumps([row.to_dict() for row in rows], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    csv_fields = [
        "run_name",
        "control_logic_version",
        "ramp_speed_mA_s",
        "total_elapsed_s",
        "current_hold_elapsed_s",
        "stress_error_rms_mpa",
        "stress_error_p95_abs_mpa",
        "stress_error_max_abs_mpa",
        "candidate_hold_sample_fraction",
        "candidate_hold_transition_count",
        "candidate_min_ramp_scale",
        "candidate_mean_ramp_scale",
        "candidate_estimated_extra_ramp_time_s",
        "high_risk_sample_count",
        "high_risk_covered_fraction",
        "stable_false_slow_fraction",
        "transformation_elapsed_s",
        "recovery_elapsed_s",
        "strain_current_curve_quality",
        "mean_candidate_correction_mm",
        "max_candidate_correction_mm",
        "replay_step_floor_accept_count",
        "run_dir",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for result in rows:
            row = result.to_dict()
            writer.writerow({field: row.get(field) for field in csv_fields})
    lines = [
        "# Mini DMA Predictive Replay Summary",
        "",
        "This is advisory replay output. It classifies saved traces and proposes dynamic ramp or hold decisions; it does not simulate a closed-loop hardware outcome.",
        "",
        "| Run | Logic | Ramp mA/s | RMS MPa | p95 MPa | Max MPa | Candidate hold % | Mean ramp scale | Extra ramp s | High-risk covered | Stable false slow |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in rows:
        high_risk = "-" if result.high_risk_covered_fraction is None else f"{result.high_risk_covered_fraction:.2f}"
        stable_slow = "-" if result.stable_false_slow_fraction is None else f"{result.stable_false_slow_fraction:.2f}"
        lines.append(
            "| "
            f"{result.run_name} | "
            f"{result.control_logic_version or '-'} | "
            f"{'' if result.ramp_speed_mA_s is None else f'{result.ramp_speed_mA_s:.2f}'} | "
            f"{'' if result.stress_error_rms_mpa is None else f'{result.stress_error_rms_mpa:.3f}'} | "
            f"{'' if result.stress_error_p95_abs_mpa is None else f'{result.stress_error_p95_abs_mpa:.3f}'} | "
            f"{'' if result.stress_error_max_abs_mpa is None else f'{result.stress_error_max_abs_mpa:.3f}'} | "
            f"{result.candidate_hold_sample_fraction:.2f} | "
            f"{result.candidate_mean_ramp_scale:.2f} | "
            f"{result.candidate_estimated_extra_ramp_time_s:.1f} | "
            f"{high_risk} | "
            f"{stable_slow} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "markdown": md_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Mini DMA current-sweep runs with the predictive controller prototype.")
    parser.add_argument("paths", nargs="+", help="Run folders, metadata.json files, or parent folders.")
    parser.add_argument("--out", type=Path, default=None, help="Directory for replay summary artifacts.")
    parser.add_argument("--window-s", type=float, default=2.5, help="Signal-estimator window in seconds.")
    args = parser.parse_args(argv)
    run_dirs = discover_quality_run_dirs(args.paths)
    tuning = PredictiveControllerTuning()
    results = [
        analyze_predictive_run(run_dir, tuning=tuning, window_s=args.window_s)
        for run_dir in run_dirs
    ]
    print(json.dumps([result.to_dict() for result in results], indent=2, ensure_ascii=False))
    if args.out is not None:
        outputs = write_predictive_replay_outputs(results, args.out)
        for label, path in outputs.items():
            print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
