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
    assert abs(summary["final_error_mpa"]) <= trace.config.controller.min_recovery_mpa
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
