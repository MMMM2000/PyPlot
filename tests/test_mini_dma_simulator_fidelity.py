from dataclasses import replace
import json

import pytest

from data_logging.mini_dma_logger.iso_stress_speed_simulator import (
    IsoStressPlantConfig, IsoStressScenario, policy_config,
    run_iso_stress_simulation, run_policy_matrix, write_policy_matrix_outputs,
)


def quiet(**kwargs):
    return IsoStressScenario("test", "Synthetic sensitivity", replace(
        IsoStressPlantConfig(), measurement_noise_mpa=0, physical_fluctuation_mpa=0,
        max_elapsed_s=5, current_start_ma=22, **kwargs,
    ))


def test_transport_delay_is_causal():
    policy = replace(policy_config("baseline"), correction_deadband_mpa=1e6, pause_min_mpa=1e6)
    direct = run_iso_stress_simulation(quiet(), policy, seed=0)
    delayed = run_iso_stress_simulation(quiet(measurement_delay_s=0.5), policy, seed=0)
    assert [r.true_stress_mpa for r in direct.rows] == [r.true_stress_mpa for r in delayed.rows]
    for index, row in enumerate(delayed.rows):
        assert row.measured_stress_mpa == direct.rows[max(0, index - 2)].measured_stress_mpa


def test_thermal_lag_reduces_early_transformation():
    policy = replace(policy_config("baseline"), correction_deadband_mpa=1e6, pause_min_mpa=1e6)
    fast = run_iso_stress_simulation(quiet(), policy, seed=0)
    slow = run_iso_stress_simulation(quiet(transformation_tau_s=5), policy, seed=0)
    assert 0 < slow.rows[-1].transformation_fraction < fast.rows[-1].transformation_fraction


def test_first_command_cannot_read_hidden_stiffness():
    # Identical observations with different latent stiffness must produce identical commands.
    policy = replace(policy_config("baseline"), correction_deadband_mpa=0.01)
    common = dict(
        transformation_contraction_mm=0, outlier_every_samples=1, outlier_mpa=1,
        motor_min_step_mm=1e-6, motor_max_step_mm=.05, motor_effective_speed_mm_s=1,
    )
    low = run_iso_stress_simulation(quiet(stiffness_mpa_per_mm=100, **common), policy, seed=0)
    high = run_iso_stress_simulation(quiet(stiffness_mpa_per_mm=2000, **common), policy, seed=0)
    first_low = next(r for r in low.rows if r.motor_delta_mm)
    first_high = next(r for r in high.rows if r.motor_delta_mm)
    assert first_low.sample_index == first_high.sample_index
    assert first_low.motor_delta_mm == first_high.motor_delta_mm
    # Ensure saturation/minimum-step clamping cannot hide a privileged-gain bug.
    assert abs(first_low.motor_delta_mm) > common["motor_min_step_mm"]
    assert abs(first_low.motor_delta_mm) < common["motor_max_step_mm"]


@pytest.mark.parametrize("field", ["measurement_delay_s", "measurement_quantum_mpa", "transformation_tau_s"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_sensitivity_rejected(field, value):
    with pytest.raises(ValueError):
        replace(IsoStressPlantConfig(), **{field: value}).validated()


def test_quantization_and_seed_generator_manifest(tmp_path):
    results = run_policy_matrix(
        scenarios=["calm"], policies=["baseline", "adaptive_response_crossing"],
        seeds=iter([3, 4]),
        plant_overrides={"max_elapsed_s": 3, "measurement_quantum_mpa": 0.5},
    )
    assert len(results) == 4
    assert all(r.measured_stress_mpa * 2 == round(r.measured_stress_mpa * 2) for x in results for r in x.rows)
    paths = write_policy_matrix_outputs(results, tmp_path)
    manifest = json.loads(paths["summary"].read_text())
    assert manifest["model_version"] == 2
    assert len(manifest["source_sha256"]) == 64
    assert [x["seed"] for x in manifest["configurations"]] == [3, 4, 3, 4]
    assert manifest["configurations"][0]["scenario"]["plant"]["measurement_quantum_mpa"] == 0.5


def test_actual_force_policy_waits_for_missing_feedback():
    from data_logging.mini_dma_logger.force_control import ForceControlAction
    from data_logging.mini_dma_logger.force_control_simulator import (
        scaled_plant_families, simulate_force_control_family,
    )
    result = simulate_force_control_family(replace(
        scaled_plant_families()[0], feedback_dropout_every_steps=11,
    ))
    assert result.metrics.dropout_count > 0
    assert result.metrics.overlap_count == 0
    assert result.metrics.completed
    for sample in result.samples:
        if not sample.feedback_fresh:
            assert sample.command_mm == 0
            assert sample.action is ForceControlAction.WAIT_FOR_SAMPLE


def test_missing_samples_do_not_count_as_settling_evidence():
    from data_logging.mini_dma_logger.force_control_simulator import (
        scaled_plant_families, simulate_force_control_family,
    )
    result = simulate_force_control_family(replace(
        scaled_plant_families()[0], feedback_dropout_every_steps=2,
    ))
    assert not result.metrics.completed  # Never receives five consecutive fresh samples.
    assert result.metrics.dropout_count > 0


def test_replay_distinguishes_conditional_from_rollout():
    import numpy as np
    from scripts.mini_dma_simulator_trace_audit import evaluate
    # A drift-free predictor gets one-step error 1, but unobserved accumulation grows.
    data = np.zeros((25, 5))
    data[:, 0] = np.arange(25)
    data[:, 3] = np.arange(25)
    metrics, arrays = evaluate(data, np.zeros(3), "heldout")
    assert metrics["one_step_rmse_g"] == 1
    assert metrics["rollout_rmse_g"] > 10
    assert np.all(arrays[3] == 0)


def test_trace_audit_reads_only_synthetic_copies(tmp_path):
    import csv
    from scripts.mini_dma_simulator_trace_audit import read_run, sha256
    (tmp_path / "metadata.json").write_text(json.dumps({
        "initial_length_mm": 10, "wire_diameter_mm": .1,
    }))
    with (tmp_path / "measurement.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["elapsed_s", "raw_position_mm", "current_measured_mA", "load_g", "stress_mpa"])
        writer.writerows((i, i / 100, 1, 0, 0) for i in range(30))
    with (tmp_path / "scale_raw.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["elapsed_s"])
        writer.writerows((i / 5,) for i in range(150))
    before = {p.name: sha256(p) for p in tmp_path.iterdir()}
    data, record = read_run(tmp_path)
    assert data.shape == (30, 5)
    assert record["measurement_interval_s"]["median"] == 1
    assert record["raw_scale_interval_s"]["median"] == pytest.approx(.2)
    assert record["stress_conversion_abs_residual_mpa"]["max"] == 0
    assert before == {p.name: sha256(p) for p in tmp_path.iterdir()}
