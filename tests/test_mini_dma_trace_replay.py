from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.mini_dma_logger.trace_replay import (
    analyze_control_trace,
    stress_mpa_from_load_g,
    write_replay_outputs,
)


def _write_run(
    run_dir: Path,
    *,
    diameter_mm: float = 0.0125,
    rows: list[dict[str, object]],
) -> None:
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "wire_diameter_mm": diameter_mm,
                "stop": {"detail": "synthetic stop"},
            }
        ),
        encoding="utf-8",
    )
    fieldnames: list[str] = [
        "elapsed_s",
        "automation_phase",
        "automation_basis",
        "automation_target_value",
        "decision",
        "result",
        "current_value",
        "error_value",
        "tolerance",
        "sensitivity_per_mm",
        "motor_step_mm",
    ]
    with (run_dir / "control_trace.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_trace_replay_flags_motor_step_only_stress_accept(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        rows=[
            {
                "elapsed_s": 1.0,
                "automation_phase": "current",
                "automation_basis": "stress_mpa",
                "automation_target_value": 30.0,
                "decision": "accept",
                "result": "reached",
                "current_value": 0.0,
                "error_value": 30.0,
                "tolerance": 45.0,
                "sensitivity_per_mm": 45000.0,
                "motor_step_mm": 0.001,
            }
        ],
    )

    result = analyze_control_trace(run_dir)

    assert result.summary.old_accept_count == 1
    assert result.summary.split_accept_count == 0
    assert result.summary.step_floor_only_accept_count == 1
    assert result.rows[0]["step_floor_only_accept"] == "true"
    assert result.rows[0]["replayed_acceptance_tolerance"] == f"{stress_mpa_from_load_g(0.005, 0.0125):.12g}"


def test_trace_replay_keeps_requested_tolerance_accepts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        rows=[
            {
                "elapsed_s": 1.0,
                "automation_phase": "current",
                "automation_basis": "load_g",
                "automation_target_value": 0.1,
                "decision": "accept",
                "result": "reached",
                "current_value": 0.096,
                "error_value": 0.004,
                "tolerance": 0.005,
                "sensitivity_per_mm": 20.0,
                "motor_step_mm": 0.001,
            }
        ],
    )

    result = analyze_control_trace(run_dir)

    assert result.summary.old_accept_count == 1
    assert result.summary.split_accept_count == 1
    assert result.summary.step_floor_only_accept_count == 0
    assert result.rows[0]["would_accept_after_split"] == "true"


def test_trace_replay_writes_csv_json_and_markdown(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "out"
    _write_run(
        run_dir,
        rows=[
            {
                "elapsed_s": 1.0,
                "automation_phase": "target_ramp",
                "automation_basis": "stress_mpa",
                "automation_target_value": 30.0,
                "decision": "accept",
                "result": "reached",
                "current_value": 0.0,
                "error_value": 1.0,
                "tolerance": 45.0,
                "sensitivity_per_mm": 45000.0,
                "motor_step_mm": 0.001,
            }
        ],
    )
    result = analyze_control_trace(run_dir)

    paths = write_replay_outputs(result, out_dir)

    assert paths["csv"].exists()
    assert paths["json"].exists()
    assert paths["markdown"].exists()
    summary = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert summary["step_floor_only_accept_count"] == 1
    assert "Motor-Step-Only" in paths["markdown"].read_text(encoding="utf-8")
