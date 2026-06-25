from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from data_logging.mini_dma_logger.full_run_simulator import (
    FULL_RUN_SCENARIOS,
    _effective_max_correction_mm,
    full_run_scenario_by_name,
    run_adaptive_control_policy_matrix,
    run_control_validation_suite,
    run_control_policy_matrix,
    run_free_strain_stress_matrix,
    run_full_mini_dma_simulation,
    run_parameter_sweep,
    run_stress_ladder_combined_policy_grid,
    run_stress_ladder_candidate_policy_comparison,
    run_stress_ladder_matrix,
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


def test_full_run_stress_is_derived_from_motor_free_strain_mismatch() -> None:
    base = full_run_scenario_by_name("baseline_first_overheating")
    config = replace(
        base,
        wire=replace(base.wire, noise_mpa=0.0, fluctuation_mpa=0.0, drift_mpa_per_s=0.0),
        zero_compression_stress=False,
    )

    trace = run_full_mini_dma_simulation(config)

    for sample in trace.samples:
        expected_stress = (
            sample.motor_mm + sample.free_length_shift_mm
        ) * trace.config.wire.elastic_stiffness_mpa_per_mm
        assert abs(sample.stress_mpa - expected_stress) <= 1e-9


def test_free_strain_fluctuation_is_physical_length_input_not_plotted_strain() -> None:
    base = full_run_scenario_by_name("realistic_first_overheating")
    config = replace(
        base,
        wire=replace(base.wire, noise_mpa=0.0, fluctuation_mpa=0.0, drift_mpa_per_s=0.0),
        free_strain_fluctuation_pct=0.20,
        free_strain_fluctuation_cycles=7.0,
        zero_compression_stress=False,
        seed=441,
    )

    trace = run_full_mini_dma_simulation(config)

    roughness_mm = [
        abs(
            sample.free_length_shift_mm
            - (
                trace.config.wire.initial_free_length_shift_mm
                + sample.transformation_fraction * trace.config.wire.transformation_contraction_mm
            )
        )
        for sample in trace.samples
    ]
    assert max(roughness_mm) >= trace.config.wire.length_mm * 0.001
    for sample in trace.samples:
        expected_stress = (
            sample.motor_mm + sample.free_length_shift_mm
        ) * trace.config.wire.elastic_stiffness_mpa_per_mm
        expected_strain = (
            trace.config.reported_strain_offset_pct
            + trace.config.reported_strain_motor_scale
            * sample.motor_mm
            / trace.config.wire.length_mm
            * 100.0
        )
        assert abs(sample.stress_mpa - expected_stress) <= 1e-9
        assert sample.strain_pct == expected_strain


def test_realistic_first_overheating_matches_reference_scale() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("realistic_first_overheating"))
    summary = trace.summary()
    target_ramp_events = [event for event in trace.events if event.phase == "target_ramp"]
    current_events = [event for event in trace.events if event.phase in {"current", "current_hold"}]

    assert trace.stop_reason == "completed"
    assert 1100.0 <= summary["total_measurement_time_s"] <= 1250.0
    assert 550.0 <= summary["current_hold_time_s"] <= 700.0
    assert 25.0 <= summary["max_abs_current_sweep_error_mpa"] <= 45.0
    assert -10.5 <= summary["strain_min_pct"] <= -9.0
    assert 0.3 <= summary["strain_max_pct"] <= 0.8
    assert 9.5 <= summary["strain_range_pct"] <= 11.0
    assert 10.0 <= summary["free_transformation_strain_range_pct"] <= 10.5
    assert summary["max_abs_free_strain_tracking_error_pct"] <= 1.25
    assert summary["mean_abs_free_strain_tracking_error_pct"] <= 0.30
    assert summary["current_hold_periods"]
    assert summary["max_correction_strain_pct"] == 0.12
    assert summary["effective_max_correction_mm"] == trace.config.wire.length_mm * 0.12 / 100.0
    assert target_ramp_events[0].target_stress_mpa < 5.0
    assert target_ramp_events[-1].target_stress_mpa == trace.config.controller.target_stress_mpa
    assert current_events[0].target_stress_mpa == trace.config.controller.target_stress_mpa
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
    large_hold_strain_spans = [
        max(sample.strain_pct for sample in group) - min(sample.strain_pct for sample in group)
        for group in hold_groups
        if max(sample.strain_pct for sample in group) - min(sample.strain_pct for sample in group) >= 1.0
    ]
    adjacent_strain_steps = [
        abs(current.strain_pct - previous.strain_pct)
        for previous, current in zip(trace.samples, trace.samples[1:])
    ]
    assert 1.0 <= max_hold_strain_span <= 2.5
    assert len(large_hold_strain_spans) >= 3
    assert max(adjacent_strain_steps) <= trace.summary()["max_correction_strain_pct"] + 1e-12
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


def test_realistic_run32_first_target_matches_reference_segment_scale() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("realistic_run32_first_target"))
    summary = trace.summary()

    assert trace.stop_reason == "completed"
    assert 400.0 <= summary["total_measurement_time_s"] <= 500.0
    assert 9.5 <= summary["strain_range_pct"] <= 10.8
    assert 0.55 <= summary["current_hold_fraction_of_measurement"] <= 0.68
    assert 30.0 <= summary["p95_abs_current_sweep_error_mpa"] <= 55.0
    assert summary["max_abs_current_sweep_error_mpa"] >= 80.0
    assert summary["scale_latency_s"] == 0.2
    assert summary["max_correction_strain_pct"] == 0.16
    assert all(trace.invariants.values())


def test_bad_co6_first_overheating_exercises_early_failure_case() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("bad_co6_first_overheating"))
    summary = trace.summary()

    assert trace.stop_reason == "wire_break"
    assert max(sample.stress_mpa for sample in trace.samples) >= 240.0
    assert summary["max_abs_current_sweep_error_mpa"] >= trace.config.controller.target_stress_mpa * 0.5
    assert summary["free_transformation_strain_range_pct"] >= summary["strain_range_pct"] * 3.0
    assert summary["current_hold_time_s"] >= 1.0
    assert summary["max_abs_correction_mm"] <= summary["effective_max_correction_mm"]
    assert trace.config.wire.length_mm == 45.869
    assert trace.config.wire.diameter_mm == 0.0151
    assert all(event.feedback_age_s >= trace.config.scale_latency_s for event in trace.events)


def test_low_strain_noisy_wire_does_not_invent_large_measured_strain() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("low_strain_noisy_first_overheating"))
    summary = trace.summary()

    assert trace.stop_reason == "completed"
    assert summary["free_transformation_strain_range_pct"] <= 0.30
    assert summary["strain_range_pct"] <= 0.50
    assert summary["max_abs_free_strain_tracking_error_pct"] <= 0.10
    assert summary["max_abs_current_sweep_error_mpa"] <= trace.config.controller.target_stress_mpa * 0.25
    assert summary["p95_abs_current_sweep_error_mpa"] <= summary["max_abs_current_sweep_error_mpa"]
    assert summary["adaptive_correction_phases"] == ["current_hold"]
    assert all(trace.invariants.values())


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
    assert summary["max_abs_correction_mm"] <= summary["effective_max_correction_mm"]
    assert summary["max_total_travel_mm"] > 0.0
    assert trace.invariants["does_not_stop_for_slack"] is True
    assert trace.invariants["no_accumulated_correction_travel_stop"] is True
    assert all(event.feedback_age_s >= trace.config.scale_latency_s for event in trace.events)


def test_stress_ladder_ramps_from_50_to_100_after_unwind_slack() -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("stress_ladder_50_100_after_unwind"))
    summary = trace.summary()
    target_ramp_events = [event for event in trace.events if event.phase == "target_ramp"]
    second_ramp_events = [event for event in target_ramp_events if event.target_stress_mpa > 50.0]

    assert trace.stop_reason == "completed"
    assert second_ramp_events
    assert max(event.target_stress_mpa for event in trace.events) == 100.0
    assert all(
        current.target_stress_mpa + 1e-12 >= previous.target_stress_mpa
        for previous, current in zip(second_ramp_events, second_ramp_events[1:])
    )
    assert min(event.processed_center_mpa for event in second_ramp_events) < 50.0
    assert max(abs(event.error_mpa) for event in second_ramp_events) <= 12.0
    assert summary["max_abs_later_target_ramp_error_mpa"] <= 12.0
    assert summary["target_ramp_event_count"] == len(target_ramp_events)
    assert summary["current_phase_event_count"] > 0
    assert any(event.phase in {"current", "current_hold"} and event.target_stress_mpa == 100.0 for event in trace.events)
    assert summary["inter_target_free_length_shift_mm"] < 0.0
    assert summary["strain_range_pct"] > 12.0
    assert summary["quality_status"] == "ok"
    assert summary["max_abs_later_target_ramp_error_fraction_of_target"] <= 0.12
    assert all(trace.invariants.values())
    assert all(event.feedback_age_s >= trace.config.scale_latency_s for event in trace.events)


def test_full_run_outputs_are_replay_shaped(tmp_path: Path) -> None:
    trace = run_full_mini_dma_simulation(full_run_scenario_by_name("realistic_first_overheating"))

    paths = write_full_run_outputs(trace, tmp_path)

    assert set(paths) >= {"measurement", "control_trace", "summary", "config", "report"}
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["scenario"] == "realistic_first_overheating"
    assert paths["control_trace"].read_text(encoding="utf-8").splitlines()[0].startswith("elapsed_s,")
    measurement_text = paths["measurement"].read_text(encoding="utf-8")
    measurement_lines = measurement_text.splitlines()
    measurement_rows = list(csv.DictReader(measurement_text.splitlines()))
    measurement_header = measurement_lines[0]
    assert "processed_center_mpa" in measurement_header
    assert "current_hold_active" in measurement_header
    assert "feedback_age_s" in measurement_header
    assert "current_set_mA" in measurement_header
    assert "current_measured_mA" in measurement_header
    assert "voltage_V" in measurement_header
    assert "resistance_ohm" in measurement_header
    assert "power_W" in measurement_header
    assert "free_transformation_contraction_mm" in measurement_header
    assert "free_transformation_strain_pct" in measurement_header
    assert "motor_strain_pct" in measurement_header
    assert "elastic_mismatch_strain_pct" in measurement_header
    assert "free_strain_tracking_error_pct" in measurement_header
    assert "quality_status" in summary
    assert "quality_score" in summary
    assert "p95_abs_current_sweep_error_mpa" in summary
    assert "adaptive_correction_phases" in summary
    assert "Quality status:" in paths["report"].read_text(encoding="utf-8")
    assert "P95 current-sweep stress error" in paths["report"].read_text(encoding="utf-8")
    target_index = measurement_header.split(",").index("target_stress_mpa")
    first_target = float(measurement_lines[1].split(",")[target_index])
    assert first_target == 0.0
    assert measurement_rows[0]["free_strain_tracking_error_pct"] == ""
    assert any(
        row["automation_phase"] in {"current", "current_hold", "current_limit_unwind"}
        and row["free_strain_tracking_error_pct"] != ""
        for row in measurement_rows
    )


def test_parameter_sweep_runs_and_writes_summary(tmp_path: Path) -> None:
    traces = run_parameter_sweep()

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 18
    assert paths["summary"].exists()
    assert paths["summary_csv"].exists()
    assert "Mini DMA full-run parameter sweep" in paths["report"].read_text(encoding="utf-8")
    assert all(trace.invariants["corrections_bounded"] for trace in traces)


def test_free_strain_stress_matrix_covers_real_run_inspired_wire_families(tmp_path: Path) -> None:
    traces = run_free_strain_stress_matrix()
    summaries = [trace.summary() for trace in traces]
    names = {trace.config.name for trace in traces}

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 24
    assert any("good_12_2_10pct" in name for name in names)
    assert any("early_19_8_9pct" in name for name in names)
    assert any("co6_bad_1pct" in name for name in names)
    assert any("weak_noisy_0p25pct" in name for name in names)
    assert any(item["configured_free_strain_fluctuation_pct"] >= 0.18 for item in summaries)
    assert any(item["scale_latency_s"] >= 0.45 for item in summaries)
    assert any(item["free_transformation_strain_range_pct"] >= 10.0 for item in summaries)
    assert any(item["free_transformation_strain_range_pct"] <= 0.45 for item in summaries)
    assert all(trace.invariants["corrections_bounded"] for trace in traces)
    assert all(trace.invariants["scale_latency_applied"] for trace in traces)
    assert paths["summary"].exists()
    assert paths["summary_csv"].exists()
    assert paths.get("plot", tmp_path / "missing").exists()


def test_control_policy_matrix_compares_caps_on_good_and_weak_wires(tmp_path: Path) -> None:
    traces = run_control_policy_matrix()
    summaries = [trace.summary() for trace in traces]
    names = {trace.config.name for trace in traces}

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 84
    assert any("good_12_2_10pct" in name for name in names)
    assert any("weak_noisy_0p25pct" in name for name in names)
    assert any("stress_ladder_50_100_after_unwind" in name for name in names)
    assert any(item["max_correction_strain_pct"] < 0.06 for item in summaries)
    assert any(item["max_correction_strain_pct"] > 0.20 for item in summaries)
    assert any(item["max_abs_later_target_ramp_error_mpa"] > 0.0 for item in summaries)
    assert all(item["stop_reason"] == "completed" for item in summaries)
    assert all(item["current_phase_event_count"] > 0 for item in summaries)
    assert all(trace.invariants["corrections_bounded"] for trace in traces)
    assert paths["summary_csv"].exists()


def test_stress_ladder_matrix_covers_representative_wires(tmp_path: Path) -> None:
    traces = run_stress_ladder_matrix()
    summaries = [trace.summary() for trace in traces]
    names = {trace.config.name for trace in traces}

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 8
    assert any("good_12_2_10pct" in name for name in names)
    assert any("early_19_8_9pct" in name for name in names)
    assert any("co6_bad_1pct" in name for name in names)
    assert any("weak_noisy_0p25pct" in name for name in names)
    assert any("thin_delayed_tiny_load" in name for name in names)
    assert any("thin_1_2_high_strain_high_hold" in name for name in names)
    assert any("stiffer_thicker_high_load" in name for name in names)
    assert all(item["target_stress_sequence_mpa"] == [50.0, 100.0] for item in summaries)
    assert all(item["inter_target_free_length_shift_mm"] < 0.0 for item in summaries)
    assert all(item["max_abs_later_target_ramp_error_mpa"] > 0.0 for item in summaries)
    assert any(item["max_abs_later_target_ramp_error_mpa"] > 40.0 for item in summaries)
    assert any(item["quality_status"] == "ok" for item in summaries)
    assert any(item["quality_status"] == "needs_tuning" for item in summaries)
    assert any("later_target_ramp_error_high" in item["quality_flags"] for item in summaries)
    assert all(item["max_abs_current_sweep_error_fraction_of_target"] >= 0.0 for item in summaries)
    assert all(item["mean_abs_free_strain_tracking_error_fraction_of_span"] >= 0.0 for item in summaries)
    assert any(item["scale_latency_s"] >= 0.45 for item in summaries)
    assert all(item["stop_reason"] == "completed" for item in summaries)
    assert all(trace.invariants["no_accumulated_correction_travel_stop"] for trace in traces)
    assert all(trace.invariants["does_not_stop_for_slack"] for trace in traces)
    assert all(trace.invariants["scale_latency_applied"] for trace in traces)
    assert paths["summary_csv"].exists()


def test_stress_ladder_candidate_policy_improves_aggregate_quality(tmp_path: Path) -> None:
    traces = run_stress_ladder_candidate_policy_comparison()
    summaries = [trace.summary() for trace in traces]

    paths = write_sweep_outputs(traces, tmp_path)

    baseline = [item for item in summaries if not item["scenario"].startswith("candidate_")]
    candidates = [item for item in summaries if item["scenario"].startswith("candidate_")]
    assert len(baseline) == len(candidates) == 8
    assert sum(item["quality_score"] for item in candidates) < sum(item["quality_score"] for item in baseline)
    assert all(item["stop_reason"] == "completed" for item in candidates)
    assert all(item["quality_status"] == "ok" for item in candidates if "stiffer_thicker" in item["scenario"])
    assert all(item["quality_status"] == "ok" for item in candidates if "stress_ladder_50_100_after_unwind" in item["scenario"])
    assert any("later_target_ramp_error_high" in item["quality_flags"] for item in candidates)
    assert paths["summary_csv"].exists()


def test_stress_ladder_combined_policy_grid_supports_small_slices(tmp_path: Path) -> None:
    traces = run_stress_ladder_combined_policy_grid(
        lead_fractions=(0.05,),
        cap_scales=(1.35,),
        adaptive_scales=(1.0,),
    )
    summaries = [trace.summary() for trace in traces]

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 8
    assert all(item["scenario"].startswith("combined_l0.05_c1.35_a1_") for item in summaries)
    assert all(item["target_stress_sequence_mpa"] == [50.0, 100.0] for item in summaries)
    assert any(item["quality_status"] == "ok" for item in summaries)
    assert any(item["quality_status"] == "needs_tuning" for item in summaries)
    assert any(item["quality_status"] == "failed" for item in summaries)
    assert any("incomplete" in item["quality_flags"] for item in summaries)
    assert paths["summary_csv"].exists()
    assert paths["policy_rank"].exists()
    assert paths["policy_plot"].exists()
    rank = json.loads(paths["policy_rank"].read_text(encoding="utf-8"))
    assert rank[0]["lead_fraction"] == 0.05
    assert rank[0]["cap_scale"] == 1.35
    assert rank[0]["adaptive_scale"] == 1.0
    assert rank[0]["case_count"] == 8


def test_adaptive_control_policy_matrix_reports_response_gated_caps(tmp_path: Path) -> None:
    traces = run_adaptive_control_policy_matrix()
    summaries = [trace.summary() for trace in traces]
    names = {trace.config.name for trace in traces}

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 25
    assert any("weak_noisy_0p25pct" in name for name in names)
    assert any("stress_ladder_50_100_after_unwind" in name for name in names)
    assert all(item["stop_reason"] == "completed" for item in summaries)
    assert any(item["adaptive_correction_cap_max_scale"] == 1.0 for item in summaries)
    assert any(item["adaptive_correction_cap_max_scale"] > 1.0 for item in summaries)
    assert all(
        item["max_abs_correction_mm"] <= item["effective_max_adaptive_correction_mm"] + 1e-12
        for item in summaries
    )
    assert any(
        item["max_observed_correction_cap_mm"] > item["effective_max_correction_mm"] + 1e-12
        for item in summaries
    )
    assert paths["summary_csv"].exists()


def test_adaptive_cap_growth_requires_observed_response() -> None:
    base = full_run_scenario_by_name("realistic_run32_first_target")
    config = replace(base, adaptive_correction_cap_max_scale=3.0)

    trace = run_full_mini_dma_simulation(config)
    hold_events = [event for event in trace.events if event.phase == "current_hold"]

    assert hold_events
    assert hold_events[0].correction_cap_mm == _effective_max_correction_mm(config)
    assert max(event.correction_cap_mm for event in hold_events) > hold_events[0].correction_cap_mm


def test_current_resume_target_crossing_is_opt_in_tradeoff() -> None:
    base = full_run_scenario_by_name("realistic_run32_first_target")
    default_trace = run_full_mini_dma_simulation(base)
    crossing_trace = run_full_mini_dma_simulation(replace(base, current_resume_requires_target_crossing=True))
    default_summary = default_trace.summary()
    crossing_summary = crossing_trace.summary()

    assert default_summary["current_resume_requires_target_crossing"] is False
    assert crossing_summary["current_resume_requires_target_crossing"] is True
    assert crossing_summary["p95_abs_current_sweep_error_mpa"] < default_summary["p95_abs_current_sweep_error_mpa"]
    assert crossing_summary["current_hold_time_s"] > default_summary["current_hold_time_s"]
    assert all(crossing_trace.invariants.values())


def test_control_validation_suite_ranks_policy_tradeoffs(tmp_path: Path) -> None:
    traces = run_control_validation_suite(
        policies=("baseline", "moderate_response", "aggressive_cap", "crossing_moderate")
    )
    summaries = [trace.summary() for trace in traces]

    paths = write_sweep_outputs(traces, tmp_path)

    assert len(traces) == 36
    assert {item["scenario"].split("__", 1)[0] for item in summaries} == {
        "validation_baseline",
        "validation_moderate_response",
        "validation_aggressive_cap",
        "validation_crossing_moderate",
    }
    assert any(item["scenario"].endswith("realistic_run32_first_target") for item in summaries)
    ladder_summaries = [
        item
        for item in summaries
        if item["target_stress_sequence_mpa"] == [50.0, 100.0]
    ]
    assert len(ladder_summaries) == 32
    assert any(item["scenario"].endswith("ladder_thin_delayed_tiny_load") for item in ladder_summaries)
    assert all(
        trace.config.target_ramp_start_mpa == 0.0
        for trace in traces
        if trace.config.target_stress_sequence_mpa == (50.0, 100.0)
    )
    assert paths["validation_rank"].exists()
    assert paths["validation_plot"].exists()
    rank = json.loads(paths["validation_rank"].read_text(encoding="utf-8"))
    assert {item["policy"] for item in rank} == {
        "baseline",
        "moderate_response",
        "aggressive_cap",
        "crossing_moderate",
    }
    assert all(item["case_count"] == 9 for item in rank)
    ranked = {item["policy"]: item for item in rank}
    assert ranked["moderate_response"]["quality_score_sum"] <= ranked["baseline"]["quality_score_sum"]
    assert ranked["crossing_moderate"]["hold_time_sum_s"] >= ranked["moderate_response"]["hold_time_sum_s"]


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
