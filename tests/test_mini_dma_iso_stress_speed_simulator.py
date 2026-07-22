from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.iso_stress_speed_simulator import (
    POLICIES,
    POLICY_BASELINE,
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
