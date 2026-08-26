from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.setup_baseline_simulator import (
    SetupBaselineDetectorConfig,
    SetupBaselineObservation,
    detect_setup_baseline,
    replay_setup_csv,
    run_setup_baseline_scenario,
    scenario_by_name,
    write_setup_baseline_outputs,
)


@pytest.mark.parametrize(
    "name",
    ["clean_piecewise", "delayed_feedback", "curved_taut_branch", "false_knee_then_plateau"],
)
def test_confirmed_scenarios_recover_zero_with_bounded_error(name: str) -> None:
    trace = run_setup_baseline_scenario(scenario_by_name(name))

    assert trace.estimate.status == "confirmed"
    assert trace.estimate.zero_position_mm is not None
    assert abs(trace.estimate.zero_position_mm - trace.scenario.zero_position_mm) <= 0.0025
    assert trace.estimate.zero_uncertainty_mm is not None
    assert trace.estimate.zero_uncertainty_mm <= 0.0025


def test_delayed_feedback_samples_are_explicitly_excluded() -> None:
    trace = run_setup_baseline_scenario(scenario_by_name("delayed_feedback"))

    assert any(not observation.fresh_after_move for observation in trace.observations)
    assert trace.estimate.confirmed is True
    assert all(point.sample_count == 3 for point in trace.estimate.settled_positions)


def test_false_knee_is_rejected_before_true_plateau_is_confirmed() -> None:
    trace = run_setup_baseline_scenario(scenario_by_name("false_knee_then_plateau"))

    assert trace.estimate.confirmed is True
    assert any(candidate.outcome == "rejected" for candidate in trace.estimate.candidates)
    assert trace.estimate.candidates[-1].outcome == "confirmed"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("ambiguous_shallow_plateau", "ambiguous"), ("no_plateau", "no_plateau")],
)
def test_unqualified_zero_is_never_silently_accepted(name: str, expected: str) -> None:
    trace = run_setup_baseline_scenario(scenario_by_name(name))

    assert trace.estimate.status == expected
    assert trace.estimate.confirmed is False
    assert trace.estimate.zero_position_mm is None


def test_candidate_requires_additional_spatial_probe_positions() -> None:
    config = SetupBaselineDetectorConfig()
    observations: list[SetupBaselineObservation] = []
    raw_values = [19.50 + 0.025 * index for index in range(9)] + [19.702]
    for move_index, raw_value in enumerate(raw_values):
        position = move_index * config.motor_step_mm
        for sample_index in range(3):
            observations.append(
                SetupBaselineObservation(
                    elapsed_s=(move_index * 3 + sample_index) * 0.202,
                    position_mm=position,
                    raw_load_g=raw_value,
                    move_index=move_index,
                    sample_index=sample_index,
                )
            )

    estimate = detect_setup_baseline(observations, config)

    assert estimate.status == "candidate_unconfirmed"
    assert estimate.additional_probe_positions_required == config.confirmation_probe_positions
    assert estimate.zero_position_mm is None


def test_replay_setup_csv_preserves_candidate_unconfirmed_result(tmp_path: Path) -> None:
    path = tmp_path / "setup.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["elapsed_s", "raw_position_mm", "raw_load_g"],
        )
        writer.writeheader()
        for move_index in range(10):
            raw_value = 19.5 + 0.025 * min(move_index, 8)
            for sample_index in range(3):
                writer.writerow(
                    {
                        "elapsed_s": (move_index * 3 + sample_index) * 0.202,
                        "raw_position_mm": move_index * 0.00125,
                        "raw_load_g": raw_value,
                    }
                )

    estimate = replay_setup_csv(path)

    assert estimate.status == "candidate_unconfirmed"
    assert estimate.additional_probe_positions_required == 3


def test_simulator_writes_machine_readable_outputs(tmp_path: Path) -> None:
    trace = run_setup_baseline_scenario(scenario_by_name("clean_piecewise"))

    paths = write_setup_baseline_outputs(trace, tmp_path)

    assert set(paths) == {"observations", "settled_positions", "candidates", "summary", "scenario"}
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["scenario"] == "clean_piecewise"
    assert summary["status"] == "confirmed"


def test_setup_baseline_simulator_cli_runs_scenario(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/mini_dma_setup_baseline_simulator.py",
            "--scenario",
            "delayed_feedback",
            "--out",
            str(tmp_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    assert payload["results"][0]["status"] == "confirmed"
    assert (tmp_path / "delayed_feedback" / "summary.json").exists()
    assert (tmp_path / "setup_baseline_matrix.md").exists()
