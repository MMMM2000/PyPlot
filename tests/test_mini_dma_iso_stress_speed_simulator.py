from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.iso_stress_speed_simulator import (
    POLICIES,
    POLICY_BASELINE,
    POLICY_CYCLE_CENTER_MOTOR,
    POLICY_PROCESSED_OBSERVATION,
    POLICY_PROPOSED,
    aggregate_summaries,
    policy_config,
    run_iso_stress_simulation,
    run_policy_matrix,
    scenario_by_name,
    write_policy_matrix_outputs,
)


def test_simulation_is_deterministic() -> None:
    scenario = scenario_by_name("prague_volatile")
    policy = policy_config(POLICY_PROPOSED)

    first = run_iso_stress_simulation(scenario, policy, seed=7)
    second = run_iso_stress_simulation(scenario, policy, seed=7)

    assert first.summary == second.summary
    assert first.rows == second.rows


@pytest.mark.parametrize("policy_name", POLICIES)
def test_policy_never_exceeds_recipe_rate(policy_name: str) -> None:
    scenario = scenario_by_name("prague_volatile")
    result = run_iso_stress_simulation(scenario, policy_config(policy_name), seed=2)

    assert result.summary.max_effective_rate_ma_s <= scenario.plant.requested_rate_ma_s + 1e-12
    assert all(0.0 <= row.rate_multiplier <= 1.0 for row in result.rows)


def test_proposed_policy_completes_calm_and_volatile_cycles() -> None:
    for scenario_name in ("calm", "prague_volatile"):
        result = run_iso_stress_simulation(
            scenario_by_name(scenario_name),
            policy_config(POLICY_PROPOSED),
            seed=0,
        )

        assert result.summary.completed
        assert result.summary.stop_reason == "completed"
        assert result.summary.max_abs_true_error_mpa < scenario_by_name(scenario_name).plant.safety_max_abs_error_mpa


def test_cycle_center_motor_breaks_stationary_hunting_without_more_stress_error() -> None:
    scenario = scenario_by_name("prague_stationary_hunting")
    baseline = run_iso_stress_simulation(
        scenario,
        policy_config(POLICY_BASELINE),
        seed=0,
    )
    candidate = run_iso_stress_simulation(
        scenario,
        policy_config(POLICY_CYCLE_CENTER_MOTOR),
        seed=0,
    )

    assert baseline.summary.completed
    assert candidate.summary.completed
    assert candidate.summary.cycle_motor_suppressions > 0
    assert candidate.summary.elapsed_s < baseline.summary.elapsed_s
    assert candidate.summary.motor_travel_mm < baseline.summary.motor_travel_mm
    assert (
        candidate.summary.p95_abs_true_error_mpa
        <= baseline.summary.p95_abs_true_error_mpa * 1.05
    )


def test_cycle_center_motor_is_inactive_on_calm_short_holds() -> None:
    scenario = scenario_by_name("calm")
    baseline = run_iso_stress_simulation(
        scenario,
        policy_config(POLICY_BASELINE),
        seed=0,
    ).summary
    candidate = run_iso_stress_simulation(
        scenario,
        policy_config(POLICY_CYCLE_CENTER_MOTOR),
        seed=0,
    ).summary

    assert candidate.cycle_motor_suppressions == 0
    assert candidate.elapsed_s == baseline.elapsed_s
    assert candidate.hold_s == baseline.hold_s
    assert candidate.p95_abs_true_error_mpa == baseline.p95_abs_true_error_mpa


def test_processed_observation_breaks_hunting_and_is_neutral_on_holdouts() -> None:
    hunting = scenario_by_name("prague_stationary_hunting")
    baseline_hunting = run_iso_stress_simulation(
        hunting,
        policy_config(POLICY_BASELINE),
        seed=0,
    ).summary
    candidate_hunting = run_iso_stress_simulation(
        hunting,
        policy_config(POLICY_PROCESSED_OBSERVATION),
        seed=0,
    ).summary

    assert candidate_hunting.completed
    assert candidate_hunting.elapsed_s < baseline_hunting.elapsed_s * 0.60
    assert candidate_hunting.hold_s < baseline_hunting.hold_s * 0.55
    assert (
        candidate_hunting.p95_abs_true_error_mpa
        <= baseline_hunting.p95_abs_true_error_mpa * 1.05
    )

    for scenario_name in (
        "calm",
        "coherent_transformation",
        "sparse_feedback",
        "heavy_tail",
    ):
        scenario = scenario_by_name(scenario_name)
        baseline = run_iso_stress_simulation(
            scenario,
            policy_config(POLICY_BASELINE),
            seed=0,
        ).summary
        candidate = run_iso_stress_simulation(
            scenario,
            policy_config(POLICY_PROCESSED_OBSERVATION),
            seed=0,
        ).summary

        assert candidate.elapsed_s == baseline.elapsed_s
        assert candidate.hold_s == baseline.hold_s
        assert candidate.p95_abs_true_error_mpa == baseline.p95_abs_true_error_mpa
        assert candidate.motor_travel_mm == baseline.motor_travel_mm


def test_matrix_contains_baseline_and_proposed_for_every_scenario() -> None:
    results = run_policy_matrix(
        scenarios=("calm", "prague_volatile"),
        policies=(POLICY_BASELINE, POLICY_PROPOSED),
        seeds=range(2),
    )
    aggregate = aggregate_summaries(results)

    assert len(results) == 8
    assert {(row["scenario"], row["policy"]) for row in aggregate} == {
        ("calm", POLICY_BASELINE),
        ("calm", POLICY_PROPOSED),
        ("prague_volatile", POLICY_BASELINE),
        ("prague_volatile", POLICY_PROPOSED),
    }


def test_matrix_outputs_are_machine_readable(tmp_path: Path) -> None:
    results = run_policy_matrix(
        scenarios=("calm",),
        policies=(POLICY_BASELINE, POLICY_PROPOSED),
        seeds=range(1),
    )

    paths = write_policy_matrix_outputs(results, tmp_path)

    assert set(paths) == {"detail", "aggregate", "comparison", "summary", "representative_trace"}
    payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert "not a hardware digital twin" in payload["model_status"]
    assert len(payload["comparison"]) == 2


def test_cli_writes_comparison_outputs(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/mini_dma_iso_stress_speed_simulator.py",
            "--scenario",
            "calm",
            "--policy",
            POLICY_BASELINE,
            "--policy",
            POLICY_PROPOSED,
            "--seeds",
            "1",
            "--out",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    assert payload["runs"] == 2
    assert (tmp_path / "policy_comparison.csv").exists()
