from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.wire_simulator import (
    DEFAULT_SCENARIOS,
    decide_robust_center,
    processed_control_signal,
    run_virtual_wire_scenario,
    scenario_by_name,
    write_scenario_matrix_report,
    write_simulation_outputs,
)


def _final_decision(name: str):
    trace = run_virtual_wire_scenario(scenario_by_name(name))
    assert trace.decisions
    return trace, trace.decisions[-1]


def test_high_bias_cloud_uses_bias_recovery_not_one_tic_containment() -> None:
    trace, decision = _final_decision("high_bias_cloud")

    assert trace.stop_reason == "completed"
    assert decision.decision == "bias_recovery"
    assert decision.robust_center_mpa == pytest.approx(63.3, abs=4.0)
    assert abs(decision.motor_step_mm) > trace.scenario.controller.motor_step_mm


def test_wide_high_cloud_can_trip_raw_safety_while_center_is_wrong() -> None:
    trace, decision = _final_decision("wide_high_cloud")

    assert trace.stop_reason == "max_stress"
    assert decision.decision == "safety_stop"
    assert decision.robust_center_mpa > trace.scenario.controller.target_stress_mpa
    assert decision.raw_max_mpa >= trace.scenario.controller.safety_max_stress_mpa


def test_wide_high_cloud_without_rail_keeps_wrong_robust_center() -> None:
    scenario = scenario_by_name("wide_high_cloud")
    scenario = replace(
        scenario,
        controller=replace(scenario.controller, safety_max_stress_mpa=None),
        expected_decision="bias_recovery",
    )

    trace = run_virtual_wire_scenario(scenario)
    decision = trace.decisions[-1]

    assert trace.stop_reason == "completed"
    assert min(sample.raw_stress_mpa for sample in trace.samples) == pytest.approx(10.0, abs=1.0)
    assert max(sample.raw_stress_mpa for sample in trace.samples) == pytest.approx(300.0, abs=1.0)
    assert decision.decision == "bias_recovery"
    assert decision.robust_center_mpa > scenario.controller.target_stress_mpa


def test_target_spanning_cloud_does_not_chase_individual_fluctuations() -> None:
    trace, decision = _final_decision("target_spanning_cloud")

    assert trace.stop_reason == "completed"
    assert decision.decision == "no_move"
    assert decision.result == "target_spanning_noise_cloud"
    assert decision.raw_min_mpa <= trace.scenario.controller.target_stress_mpa <= decision.raw_max_mpa
    assert decision.motor_step_mm == 0.0


def test_transformation_contraction_bias_moves_from_robust_center() -> None:
    trace, decision = _final_decision("transformation_bias")

    assert trace.stop_reason == "completed"
    assert decision.decision == "bias_recovery"
    assert decision.robust_center_mpa > trace.scenario.controller.target_stress_mpa
    assert decision.motor_step_mm < 0.0


def test_sign_crossing_robust_center_waits_before_reversing() -> None:
    trace, decision = _final_decision("sign_crossing_reversal")

    assert trace.stop_reason == "completed"
    assert decision.decision == "wait_reversal"
    assert decision.result == "sign_crossing_confirmation"
    assert decision.motor_step_mm == pytest.approx(trace.scenario.controller.motor_step_mm)


def test_wire_break_contact_loss_condition_stops_trace() -> None:
    trace, decision = _final_decision("wire_break")

    assert trace.stop_reason == "wire_break"
    assert decision.decision == "safety_stop"
    assert decision.result == "wire_break"
    assert trace.samples[-1].status == "wire_break"


def test_robust_decision_uses_raw_samples_for_absurd_jump_safety() -> None:
    scenario = scenario_by_name("target_spanning_cloud")
    samples = list(run_virtual_wire_scenario(scenario).samples[:5])
    samples[-1] = samples[-1].__class__(
        **{**samples[-1].__dict__, "raw_stress_mpa": samples[-2].raw_stress_mpa + 250.0}
    )

    decision = decide_robust_center(samples, scenario.controller)

    assert decision.decision == "safety_stop"
    assert decision.result in {"contact_loss", "max_stress"}


def test_simulator_writes_machine_readable_outputs(tmp_path: Path) -> None:
    trace = run_virtual_wire_scenario(scenario_by_name("high_bias_cloud"))

    paths = write_simulation_outputs(trace, tmp_path)

    assert set(paths) == {"measurement", "control_trace", "summary", "scenario"}
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["scenario"] == "high_bias_cloud"
    assert summary["final_decision"] == "bias_recovery"
    with paths["control_trace"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert rows[-1]["automation_basis"] == "stress_mpa"


def test_wire_simulator_cli_runs_named_scenario(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/mini_dma_wire_simulator.py",
            "--scenario",
            "target_spanning_cloud",
            "--out",
            str(tmp_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    assert payload["scenarios"][0]["scenario"] == "target_spanning_cloud"
    assert (tmp_path / "measurement.csv").exists()
    assert (tmp_path / "control_trace.csv").exists()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("low_noise_centered", "no_move"),
        ("high_raw_centered", "no_move"),
        ("high_raw_far_above", "bias_recovery"),
        ("transformation_current_rise", "bias_recovery"),
        ("reverse_current_unwind", "bias_recovery"),
        ("slack_after_unwind", "bias_recovery"),
        ("bad_low_apparent_stiffness", "bias_recovery"),
        ("thin_wire_tiny_load", "no_move"),
        ("thick_wire_larger_load", "no_move"),
        ("delayed_scale_feedback", "bias_recovery"),
    ],
)
def test_processed_center_scenario_matrix(name: str, expected: str) -> None:
    trace, decision = _final_decision(name)

    assert name in DEFAULT_SCENARIOS
    assert trace.stop_reason == "completed"
    assert decision.decision == expected
    assert decision.processed_fresh is True


def test_processed_signal_reports_center_noise_slope_samples_and_freshness() -> None:
    trace = run_virtual_wire_scenario(scenario_by_name("high_raw_far_above"))

    signal = processed_control_signal(trace.samples, trace.scenario.controller)

    assert signal.sample_count >= 2
    assert signal.fresh is True
    assert signal.center_mpa > trace.scenario.controller.target_stress_mpa
    assert signal.noise_mpa > 0.0
    assert signal.raw_min_mpa < signal.raw_max_mpa


def test_slack_and_bad_low_stiffness_scenarios_stay_bounded() -> None:
    for name in ("slack_after_unwind", "bad_low_apparent_stiffness"):
        trace, decision = _final_decision(name)

        assert decision.decision == "bias_recovery"
        assert abs(decision.motor_step_mm) <= trace.scenario.controller.max_correction_mm
        assert decision.endpoint_recovered is False


def test_scenario_matrix_report_writes_machine_and_human_outputs(tmp_path: Path) -> None:
    traces = [
        run_virtual_wire_scenario(scenario_by_name("low_noise_centered")),
        run_virtual_wire_scenario(scenario_by_name("high_raw_far_above")),
    ]

    paths = write_scenario_matrix_report(traces, tmp_path)

    assert Path(paths["summary"]).exists()
    assert Path(paths["report"]).read_text(encoding="utf-8").startswith("# Mini DMA")
    if "plot" in paths:
        assert Path(paths["plot"]).exists()
