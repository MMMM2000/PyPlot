from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.mini_dma_logger.real_sim_compare import (
    compare_measurements,
    load_measurement_points,
    summarize_points,
)


def _write_measurement(path: Path, *, sim: bool) -> None:
    fields = [
        "elapsed_s",
        "automation_phase",
        "strain_pct",
        "stress_mpa",
        "current_set_mA",
        "current_measured_mA",
    ]
    if sim:
        fields += ["target_stress_mpa", "processed_center_mpa", "current_hold_active"]
    else:
        fields += ["automation_target_value"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, phase in enumerate(["target_ramp", "current", "current_hold", "current_hold", "current"]):
            row = {
                "elapsed_s": float(index),
                "automation_phase": phase,
                "strain_pct": -float(index),
                "stress_mpa": 50.0 + index,
                "current_set_mA": 10.0 + index,
                "current_measured_mA": 10.0 + index,
            }
            if sim:
                row["target_stress_mpa"] = 50.0
                row["processed_center_mpa"] = 50.0 + index * 0.5
                row["current_hold_active"] = str(phase == "current_hold").lower()
            else:
                row["automation_target_value"] = 50.0
            writer.writerow(row)


def test_real_sim_compare_normalizes_real_and_sim_measurements(tmp_path: Path) -> None:
    real = tmp_path / "real.csv"
    sim = tmp_path / "sim.csv"
    _write_measurement(real, sim=False)
    _write_measurement(sim, sim=True)

    real_points = load_measurement_points(real)
    sim_points = load_measurement_points(sim)
    real_summary = summarize_points(real_points)
    sim_summary = summarize_points(sim_points)

    assert real_points[0].target_mpa == 50.0
    assert sim_points[0].target_mpa == 50.0
    assert real_summary["strain_span_pct"] == 4.0
    assert sim_summary["strain_span_pct"] == 4.0
    assert real_summary["hold_group_count"] == 1
    assert sim_summary["hold_group_count"] == 1
    assert sim_summary["max_abs_stress_error_mpa"] == 2.0


def test_real_sim_compare_writes_artifacts(tmp_path: Path) -> None:
    real = tmp_path / "real.csv"
    sim = tmp_path / "sim.csv"
    _write_measurement(real, sim=False)
    _write_measurement(sim, sim=True)

    paths = compare_measurements(real, sim, tmp_path / "out")

    assert set(paths) == {"summary", "report", "plot"}
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["real"]["row_count"] == 5
    assert "Strain vs current" not in paths["report"].read_text(encoding="utf-8")
    assert paths["plot"].exists()
    assert paths["plot"].stat().st_size > 0


def test_real_sim_compare_can_filter_by_max_target(tmp_path: Path) -> None:
    real = tmp_path / "real.csv"
    sim = tmp_path / "sim.csv"
    _write_measurement(real, sim=False)
    _write_measurement(sim, sim=True)
    with real.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "elapsed_s",
                "automation_phase",
                "strain_pct",
                "stress_mpa",
                "current_set_mA",
                "current_measured_mA",
                "automation_target_value",
            ],
        )
        writer.writerow(
            {
                "elapsed_s": 99.0,
                "automation_phase": "target_ramp",
                "strain_pct": -99.0,
                "stress_mpa": 100.0,
                "current_set_mA": 1.0,
                "current_measured_mA": 1.0,
                "automation_target_value": 100.0,
            }
        )

    paths = compare_measurements(real, sim, tmp_path / "out-filtered", max_target_mpa=50.0)

    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["max_target_mpa"] == 50.0
    assert summary["real"]["row_count"] == 5
