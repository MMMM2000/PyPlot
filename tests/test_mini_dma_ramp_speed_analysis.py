from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.mini_dma_logger.ramp_speed_analysis import (
    analyze_ramp_speed_run,
    discover_run_dirs,
    write_ramp_speed_outputs,
)


def _write_run(run_dir: Path, *, ramp_speed: float, stresses: list[float]) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 19/7",
                "wire_diameter_mm": 0.0125,
                "controlled_current_sweep": {
                    "target_start": 30.0,
                    "current_start_mA": 1.0,
                    "current_end_mA": 70.0,
                    "current_ramp_rate_mA_s": ramp_speed,
                },
                "stop": {"reason": "recipe_completed", "detail": "done"},
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "elapsed_s",
                "automation_phase",
                "automation_target_value",
                "stress_mpa",
                "current_measured_mA",
            ],
        )
        writer.writeheader()
        for index, stress in enumerate(stresses):
            writer.writerow(
                {
                    "elapsed_s": float(index),
                    "automation_phase": "current_hold" if index == 2 else "current",
                    "automation_target_value": 30.0,
                    "stress_mpa": stress,
                    "current_measured_mA": 1.0 + index,
                }
            )
    (run_dir / "control_trace.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_basis,automation_target_value,decision,result,current_value,error_value,tolerance,sensitivity_per_mm,motor_step_mm",
                "1.0,current,stress_mpa,30.0,accept,reached,30.0,0.0,0.4,45000.0,0.001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_analyze_ramp_speed_run_reports_precision_and_time(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-0p6"
    _write_run(run_dir, ramp_speed=0.6, stresses=[30.0, 32.0, 31.0, 29.0])

    metrics = analyze_ramp_speed_run(run_dir)

    assert metrics.ramp_speed_mA_s == 0.6
    assert metrics.stress_error_sample_count == 4
    assert metrics.stress_error_max_abs_mpa == 2.0
    assert metrics.current_hold_sample_count == 1
    assert metrics.current_hold_elapsed_s == 1.0
    assert metrics.stop_reason == "recipe_completed"


def test_discover_run_dirs_accepts_bench_summary_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "run-0p8"
    _write_run(run_dir, ramp_speed=0.8, stresses=[30.0])
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"runs": [{"metadata_path": str(run_dir / "metadata.json")}]}),
        encoding="utf-8",
    )

    assert discover_run_dirs([summary_path]) == [run_dir.resolve()]


def test_write_ramp_speed_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1p0"
    _write_run(run_dir, ramp_speed=1.0, stresses=[30.0, 35.0])
    metrics = [analyze_ramp_speed_run(run_dir)]

    outputs = write_ramp_speed_outputs(metrics, tmp_path / "out")

    assert outputs["csv"].exists()
    assert outputs["json"].exists()
    assert outputs["markdown"].exists()
    assert "ramp_speed_mA_s" in outputs["markdown"].read_text(encoding="utf-8")
