from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from data_logging.mini_dma_logger.full_run_simulator import (
    FULL_RUN_SCENARIOS,
    full_run_scenario_by_name,
    run_full_mini_dma_simulation,
    run_parameter_sweep,
    write_full_run_outputs,
    write_sweep_outputs,
)


def test_full_run_baseline_preserves_invariants() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("baseline_first_overheating"))

    assert trace.stop_reason == "completed"
    assert trace.events
    assert all(trace.invariants.values())
    assert any(event.phase == "current_hold" for event in trace.events)
    assert all(not event.cruise_allowed for event in trace.events)


def test_realistic_first_overheating_matches_reference_scale() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("realistic_first_overheating"))
    summary = trace.summary()

    assert trace.stop_reason == "completed"
    assert 850.0 <= summary["total_measurement_time_s"] <= 1100.0
    assert 350.0 <= summary["current_hold_time_s"] <= 550.0
    assert 25.0 <= summary["max_abs_current_sweep_error_mpa"] <= 45.0
    assert -10.5 <= summary["strain_min_pct"] <= -9.0
    assert 0.3 <= summary["strain_max_pct"] <= 0.8
    assert 9.5 <= summary["strain_range_pct"] <= 11.0
    assert summary["current_hold_periods"]
    assert all(trace.invariants.values())


def test_realistic_current_holds_keep_current_fixed_while_motor_strain_changes() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("realistic_first_overheating"))
    samples_by_time = {round(sample.elapsed_s, 9): sample for sample in trace.samples}
    hold_groups = []
    current_group = []
    for event in trace.events:
        sample = samples_by_time.get(round(event.elapsed_s, 9))
        if event.phase == "current_hold" and sample is not None:
            current_group.append(sample)
        elif current_group:
            hold_groups.append(current_group)
            current_group = []
    if current_group:
        hold_groups.append(current_group)

    assert hold_groups
    assert all(
        max(sample.current_ma for sample in group) - min(sample.current_ma for sample in group) <= 1e-9
        for group in hold_groups
    )
    max_hold_strain_span = max(
        max(sample.strain_pct for sample in group) - min(sample.strain_pct for sample in group)
        for group in hold_groups
    )
    adjacent_strain_steps = [
        abs(current.strain_pct - previous.strain_pct)
        for previous, current in zip(trace.samples, trace.samples[1:])
    ]
    assert 0.02 <= max_hold_strain_span <= 0.35
    assert max(adjacent_strain_steps) <= 0.08
    assert trace.summary()["max_total_travel_mm"] <= 10.0
    for sample in trace.samples:
        expected_strain = (
            trace.config.reported_strain_offset_pct
            + trace.config.reported_strain_motor_scale
            * sample.motor_mm
            / trace.config.wire.length_mm
            * 100.0
        )
        assert sample.strain_pct == expected_strain


def test_full_run_endpoint_waits_only_until_processed_recovered() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("transformation_recovery"))

    endpoint_waits = [event for event in trace.events if event.result == "endpoint_waiting_for_recovery"]

    assert endpoint_waits
    assert all(not event.endpoint_recovered for event in endpoint_waits)
    assert trace.invariants["endpoint_completion_recovered"] is True


def test_full_run_slack_after_unwind_keeps_taking_up_tension() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("slack_after_unwind_takeup"))
    summary = trace.summary()

    assert trace.stop_reason == "completed"
    assert trace.events[-1].endpoint_recovered
    assert summary["max_abs_correction_mm"] <= trace.config.controller.max_correction_mm
    assert summary["max_total_travel_mm"] > 0.0
    assert trace.invariants["does_not_stop_for_slack"] is True
    assert trace.invariants["no_accumulated_correction_travel_stop"] is True
    assert all(event.feedback_age_s >= trace.config.scale_latency_s for event in trace.events)


def test_full_run_outputs_are_replay_shaped(tmp_path: Path) -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("thin_wire_delayed_feedback"))

    paths = write_full_run_outputs(trace, tmp_path)

    assert set(paths) >= {"measurement", "control_trace", "summary", "config", "report"}
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["scenario"] == "thin_wire_delayed_feedback"
    assert paths["control_trace"].read_text(encoding="utf-8").splitlines()[0].startswith("elapsed_s,")
    measurement_header = paths["measurement"].read_text(encoding="utf-8").splitlines()[0]
    assert "processed_center_mpa" in measurement_header
    assert "current_hold_active" in measurement_header
    assert "feedback_age_s" in measurement_header
    assert "current_set_mA" in measurement_header
    assert "current_measured_mA" in measurement_header
    assert "voltage_V" in measurement_header
    assert "resistance_ohm" in measurement_header
    assert "power_W" in measurement_header


def test_parameter_sweep_runs_and_writes_summary(tmp_path: Path) -> None:
    traces = run_parameter_sweep()

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 18
    assert paths["summary"].exists()
    assert "Mini DMA full-run parameter sweep" in paths["report"].read_text(encoding="utf-8")
    assert all(trace.invariants["corrections_bounded"] for trace in traces)


def test_full_run_cli_runs_named_scenario(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/mini_dma_full_run_simulator.py",
            "--scenario",
            "baseline_first_overheating",
            "--out",
            str(tmp_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    assert payload["runs"][0]["scenario"] == "baseline_first_overheating"
    assert (tmp_path / "measurement.csv").exists()
    assert (tmp_path / "control_trace.csv").exists()


def test_all_named_full_run_scenarios_are_registered() -> None:
    assert {full_run_scenario_by_name(name).name for name in FULL_RUN_SCENARIOS} == set(FULL_RUN_SCENARIOS)
