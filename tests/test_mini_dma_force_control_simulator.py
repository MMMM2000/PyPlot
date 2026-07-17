from __future__ import annotations

import pytest

from data_logging.mini_dma_logger.force_control import ForceControlAction
from data_logging.mini_dma_logger.force_control_simulator import (
    ForceControlPlantFamily,
    scaled_plant_families,
    simulate_force_control_family,
    simulate_scaled_plant_families,
)


def test_adaptive_policy_completes_scaled_families_without_command_overlap() -> None:
    results = simulate_scaled_plant_families()

    assert results == simulate_scaled_plant_families()
    assert len(results) >= 4
    assert all(result.metrics.completed for result in results)
    assert all(result.metrics.overlap_count == 0 for result in results)
    assert all(result.metrics.max_commands_in_flight <= 1 for result in results)
    assert all(result.metrics.command_count > 0 for result in results)
    assert all(
        result.metrics.max_command_normalized <= result.family.max_command_normalized
        for result in results
    )
    assert all(result.metrics.p95_normalized_error < 0.8 for result in results)


def test_scaled_equivalent_plants_have_equivalent_dimensionless_metrics() -> None:
    base = ForceControlPlantFamily(
        name="scale-one",
        load_scale_g=1.0,
        load_per_mm_g=2.0,
        response_delay_steps=2,
    )
    scaled = ForceControlPlantFamily(
        name="scale-fifty",
        load_scale_g=50.0,
        load_per_mm_g=100.0,
        response_delay_steps=2,
    )

    first = simulate_force_control_family(base)
    second = simulate_force_control_family(scaled)

    assert first.metrics.completed and second.metrics.completed
    assert first.metrics.completion_step == second.metrics.completion_step
    assert first.metrics.p95_normalized_error == pytest.approx(
        second.metrics.p95_normalized_error
    )
    assert first.metrics.max_command_normalized == pytest.approx(
        second.metrics.max_command_normalized
    )


def test_held_current_recovery_learns_from_a_severely_low_initial_gain() -> None:
    family = ForceControlPlantFamily(
        name="held_current_gain_recovery",
        load_scale_g=1.326,
        load_per_mm_g=2.55,
        initial_gain_ratio=0.18,
        initial_load_normalized=0.82,
        tolerance_normalized=0.008,
        noise_normalized=0.008,
        quantization_normalized=0.008,
        response_delay_steps=2,
        response_observation_steps=9,
        disturbance_normalized=0.08,
        disturbance_start_step=70,
        disturbance_ramp_steps=8,
        max_steps=500,
    )

    result = simulate_force_control_family(family)

    assert result.metrics.completed is True
    assert result.metrics.recovered is True
    assert result.metrics.overlap_count == 0
    assert result.metrics.command_count <= 10


def test_delayed_transport_keeps_policy_commands_serialized() -> None:
    family = ForceControlPlantFamily(
        name="long-delay",
        load_scale_g=4.0,
        load_per_mm_g=2.5,
        response_delay_steps=7,
        noise_normalized=0.003,
        quantization_normalized=0.004,
    )

    result = simulate_force_control_family(family)

    assert result.metrics.completed
    assert result.metrics.overlap_count == 0
    assert result.metrics.max_commands_in_flight == 1
    assert any(
        sample.action is ForceControlAction.WAIT_FOR_MOTOR for sample in result.samples
    )


def test_transformation_family_reports_recovery_after_disturbance() -> None:
    family = next(
        family
        for family in scaled_plant_families()
        if family.name == "transformation_recovery"
    )

    result = simulate_force_control_family(family)

    assert result.metrics.completed
    assert result.metrics.recovered
    assert result.metrics.recovery_steps is not None
    assert result.metrics.recovery_steps > 0
    assert result.metrics.overlap_count == 0
