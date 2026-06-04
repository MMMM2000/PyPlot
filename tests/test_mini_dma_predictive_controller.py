from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.predictive_controller import (
    PredictiveControllerTuning,
    PredictivePhase,
    PredictiveSnapshot,
    advise_predictive_control,
    classify_predictive_phase,
    estimate_predictive_features,
    select_predictive_motor_step,
)
from data_logging.mini_dma_logger.predictive_replay import (
    analyze_predictive_run,
    write_predictive_replay_outputs,
)


def test_predictive_phase_detects_transformation_and_slows_current_ramp() -> None:
    samples = [
        PredictiveSnapshot(
            elapsed_s=float(index),
            stress_mpa=50.0 + index * 3.0,
            target_mpa=50.0,
            current_mA=10.0 + index,
            strain_pct=0.1 + index * 0.04,
        )
        for index in range(5)
    ]

    features = estimate_predictive_features(samples)
    assert features is not None
    phase, confidence = classify_predictive_phase(features)
    advice = advise_predictive_control(features)

    assert phase == PredictivePhase.TRANSFORMATION
    assert confidence > 0.5
    assert advice.hold_current is False
    assert advice.ramp_scale < 1.0
    assert advice.moving_away is True


def test_predictive_phase_marks_recovery_without_forcing_hold() -> None:
    samples = [
        PredictiveSnapshot(
            elapsed_s=float(index),
            stress_mpa=58.0 - index * 1.5,
            target_mpa=50.0,
            current_mA=40.0,
            strain_pct=0.5,
        )
        for index in range(5)
    ]

    features = estimate_predictive_features(samples)
    assert features is not None
    phase, _confidence = classify_predictive_phase(features)
    advice = advise_predictive_control(features)

    assert phase == PredictivePhase.RECOVERY
    assert advice.hold_current is False
    assert 0.25 < advice.ramp_scale < 1.0


def test_predictive_motor_step_reduces_error_without_overshoot() -> None:
    advice = select_predictive_motor_step(
        stress_error_mpa=8.0,
        sensitivity_mpa_per_mm=1000.0,
        phase=PredictivePhase.STABLE_ELASTIC,
        tuning=PredictiveControllerTuning(max_correction_mm=0.02),
    )

    assert advice.correction_mm > 0.0
    assert advice.target_space_direction < 0.0
    assert abs(advice.predicted_error_mpa) < 8.0
    assert advice.predicted_error_mpa > -0.25


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _synthetic_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "synthetic_predictive_run"
    run_dir.mkdir()
    metadata = {
        "sample_name": "synthetic",
        "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "synthetic"},
        "initial_length_mm": 33.68,
        "wire_diameter_mm": 0.0191,
        "recipe_mode": "current_sweep_stress",
        "stop": {"reason": "recipe_completed", "detail": "Recipe completed."},
        "heating": {"voltage_limit_v": 32.05},
        "control_logic": {"version": "test-predictive"},
        "controlled_current_sweep": {
            "target_mpa": 50.0,
            "current_ramp_rate_mA_s": 0.4,
            "current_start_mA": 1.0,
            "current_end_mA": 80.0,
        },
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    measurement_rows: list[dict[str, object]] = []
    stresses = [50, 51, 53, 57, 63, 60, 55, 51, 50, 49, 50, 51]
    for index, stress in enumerate(stresses):
        current = 1.0 + index * 3.0
        measurement_rows.append(
            {
                "elapsed_s": float(index),
                "timestamp_utc": "2026-06-04 00:00:00",
                "recipe_mode": "current_sweep_stress",
                "automation_phase": "current_hold" if 4 <= index <= 6 else "current",
                "automation_basis": "stress_mpa",
                "automation_target_value": 50.0,
                "stress_mpa": float(stress),
                "strain_pct": index * 0.04,
                "current_set_mA": current,
                "current_measured_mA": current,
                "voltage_V": 10.0 + index * 0.2,
            }
        )
    _write_csv(run_dir / "measurement.csv", measurement_rows)
    trace_rows = [
        {
            "elapsed_s": 1.0,
            "timestamp_utc": "2026-06-04 00:00:01",
            "recipe_mode": "current_sweep_stress",
            "task_text": "synthetic",
            "automation_phase": "current",
            "automation_basis": "stress_mpa",
            "automation_target_value": 50.0,
            "plateau_index": 1,
            "decision": "correction",
            "current_value": 57.0,
            "error_value": -7.0,
            "tolerance": 0.25,
            "sensitivity_per_mm": 1000.0,
            "motor_step_mm": 0.00125,
            "correction_mm": 0.005,
            "backlash_mm": 0.0,
            "command_speed_mm_s": 0.05,
            "required_fresh_samples": 1,
            "post_move_sample_count": 0,
            "target_mm": 0.0,
            "effective_target_mm": 0.0,
            "result": "move_sent",
            "reason": "gated",
        }
    ]
    _write_csv(run_dir / "control_trace.csv", trace_rows)
    return run_dir


def test_predictive_replay_summarizes_synthetic_run(tmp_path: Path) -> None:
    run_dir = _synthetic_run_dir(tmp_path)

    replay = analyze_predictive_run(run_dir)
    outputs = write_predictive_replay_outputs([replay], tmp_path / "out")

    assert replay.high_risk_sample_count > 0
    assert replay.high_risk_covered_fraction is not None
    assert replay.high_risk_covered_fraction > 0.8
    assert replay.candidate_mean_ramp_scale < 1.0
    assert replay.transformation_elapsed_s > 0.0
    assert replay.mean_candidate_correction_mm is not None
    assert outputs["json"].exists()
    assert outputs["csv"].exists()
    assert outputs["markdown"].exists()
