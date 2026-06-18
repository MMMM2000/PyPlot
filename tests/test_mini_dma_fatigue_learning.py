from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.fatigue_learning import (
    analyze_fatigue_runs,
    extract_run_features,
)
from scripts.mini_dma_fatigue_learning import main as fatigue_learning_main


def _write_fatigue_run(
    run_dir: Path,
    *,
    transform_current_mA: float = 35.0,
    rows_per_leg: int = 80,
    recipe_mode: str = "current_sweep_stress",
    basis: str = "stress_mpa",
    stop_reason: str = "recipe_completed",
    target_stress_mpa: float = 150.0,
    current_start_mA: float = 1.0,
    current_end_mA: float = 60.0,
) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 19/8",
                "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "19/8"},
                "initial_length_mm": 47.0,
                "wire_diameter_mm": 0.0089,
                "recipe_mode": recipe_mode,
                "recipe_summary": "iso-stress fatigue learning fixture",
                "stop": {"reason": stop_reason, "detail": "done"},
                "control_logic": {"version": "2026-06-17.1", "fingerprint": "sha256:test"},
                "source_control": {"branch": "main", "commit": "abc123"},
                "heating": {"voltage_limit_v": 32.0},
                "controlled_current_sweep": {
                    "mode": recipe_mode,
                    "basis": basis,
                    "target_start": target_stress_mpa,
                    "target_end": target_stress_mpa,
                    "current_start_mA": current_start_mA,
                    "current_end_mA": current_end_mA,
                    "current_ramp_rate_mA_s": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    currents_up = [
        current_start_mA + (current_end_mA - current_start_mA) * index / (rows_per_leg - 1)
        for index in range(rows_per_leg)
    ]
    currents = currents_up + list(reversed(currents_up))
    with (run_dir / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "elapsed_s",
                "automation_phase",
                "automation_basis",
                "automation_target_value",
                "stress_mpa",
                "strain_pct",
                "current_set_mA",
                "current_measured_mA",
                "resistance_ohm",
                "voltage_V",
            ],
        )
        writer.writeheader()
        for index, current in enumerate(currents):
            transform_progress = 1.0 / (1.0 + math.exp(-(current - transform_current_mA) * 2.8))
            stress = target_stress_mpa + ((index % 7) - 3) * 0.35
            if 45 <= index < 50:
                phase = "current_hold"
                stress += 1.5 - (index - 45) * 0.4
            else:
                phase = "current"
            writer.writerow(
                {
                    "elapsed_s": index * 0.5,
                    "automation_phase": phase,
                    "automation_basis": basis,
                    "automation_target_value": target_stress_mpa,
                    "stress_mpa": stress,
                    "strain_pct": 0.015 * current + 0.35 * transform_progress,
                    "current_set_mA": current,
                    "current_measured_mA": current * 0.995,
                    "resistance_ohm": 120.0 + 0.20 * current + 10.0 * transform_progress,
                    "voltage_V": 2.5,
                }
            )


def test_fatigue_learning_groups_repeated_stress_sweeps_and_suggests_priors(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _write_fatigue_run(root / "run01", transform_current_mA=34.0)
    _write_fatigue_run(root / "run02", transform_current_mA=35.0)
    _write_fatigue_run(root / "run03", transform_current_mA=36.0)
    _write_fatigue_run(root / "too-short", transform_current_mA=35.0, rows_per_leg=10)

    payload = analyze_fatigue_runs([root])

    assert payload["run_count"] == 4
    assert payload["included_run_count"] == 3
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["included_count"] == 3
    assert group["excluded_count"] == 1
    assert group["confidence"] == "low"
    assert group["suggested_priors"]["review_only"] is True
    assert group["suggested_priors"]["apply_to_live_control"] is False
    assert group["target_stress_mpa_median"] == pytest.approx(150.0)
    assert group["current_end_mA_median"] == pytest.approx(60.0)
    assert group["transformation_current_mA_median"] == pytest.approx(35.0, abs=1.2)
    assert group["suggested_priors"]["expected_transformation_window_mA"] >= 2.0


def test_fatigue_learning_excludes_non_stress_basis_but_keeps_reason(tmp_path: Path) -> None:
    run_dir = tmp_path / "strain-run"
    _write_fatigue_run(run_dir, recipe_mode="current_sweep_strain", basis="strain_pct")

    feature = extract_run_features(run_dir)

    assert feature.included is False
    assert "recipe_mode:current_sweep_strain" in feature.exclusion_reasons
    assert "basis:strain_pct" in feature.exclusion_reasons


def test_fatigue_learning_cli_writes_json_csv_and_report(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    out_dir = tmp_path / "learning"
    _write_fatigue_run(root / "run01", transform_current_mA=34.5)
    _write_fatigue_run(root / "run02", transform_current_mA=35.5)
    _write_fatigue_run(root / "failed-setup", rows_per_leg=8, stop_reason="failed_setup")

    assert fatigue_learning_main([str(root), "--out-dir", str(out_dir)]) == 0

    summary_path = out_dir / "fatigue_learning_summary.json"
    runs_path = out_dir / "fatigue_learning_runs.csv"
    report_path = out_dir / "fatigue_learning_report.md"
    assert summary_path.exists()
    assert runs_path.exists()
    assert report_path.exists()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["run_count"] == 3
    assert payload["included_run_count"] == 2
    assert "review-only" in report_path.read_text(encoding="utf-8")
    assert "failed-setup" in report_path.read_text(encoding="utf-8")
