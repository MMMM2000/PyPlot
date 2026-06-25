from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.mini_dma_logger.real_run_reference import (
    collect_real_run_references,
    write_reference_outputs,
)


def _write_run(folder: Path, *, include: bool, strain_values: list[float], p95_error: float) -> None:
    folder.mkdir(parents=True)
    (folder / "run_quality.json").write_text(
        json.dumps(
            {
                "run_dir": str(folder),
                "sample_name": folder.name,
                "recipe_mode": "current_sweep_stress",
                "run_type": "normal_measurement",
                "include_in_optimization_summary": include,
                "stress_error_p95_abs_mpa": p95_error,
                "stress_error_by_phase": {
                    "target_ramp": {"max_abs_mpa": 4.0},
                    "current": {"max_abs_mpa": 8.0},
                    "current_hold": {"max_abs_mpa": 6.0},
                },
                "current_hold_fraction": 0.5,
                "current_measured_max_mA": 80.0,
            }
        ),
        encoding="utf-8",
    )
    with (folder / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "strain_pct",
                "stress_mpa",
                "current_measured_mA",
                "automation_target_value",
                "automation_phase",
            ],
        )
        writer.writeheader()
        phases = ["target_ramp", "current", "current_hold"]
        for index, strain in enumerate(strain_values):
            writer.writerow(
                {
                    "strain_pct": strain,
                    "stress_mpa": 50.0 + index,
                    "current_measured_mA": 10.0 + index,
                    "automation_target_value": 50.0,
                    "automation_phase": phases[index % len(phases)],
                }
            )


def test_real_run_reference_collects_and_sorts_measured_runs(tmp_path: Path) -> None:
    _write_run(tmp_path / "good_high_strain", include=True, strain_values=[0.0, -10.0, -2.0], p95_error=12.0)
    _write_run(tmp_path / "weak_wire", include=True, strain_values=[0.0, -0.2, -0.1], p95_error=40.0)
    _write_run(tmp_path / "excluded", include=False, strain_values=[0.0, -20.0], p95_error=1.0)

    rows = collect_real_run_references(tmp_path)

    assert len(rows) == 3
    assert rows[0]["folder"] == "good_high_strain"
    assert rows[0]["strain_span_pct"] == 10.0
    assert rows[0]["target_min_mpa_meas"] == 50.0
    assert rows[0]["target_max_mpa_meas"] == 50.0
    assert rows[0]["current_hold_row_fraction"] == 1.0 / 3.0
    assert rows[-1]["folder"] == "excluded"


def test_real_run_reference_writes_machine_and_human_artifacts(tmp_path: Path) -> None:
    _write_run(tmp_path / "good_high_strain", include=True, strain_values=[0.0, -10.0, -2.0], p95_error=12.0)
    rows = collect_real_run_references(tmp_path)

    paths = write_reference_outputs(rows, tmp_path / "out")

    assert set(paths) == {"json", "csv", "report", "plot"}
    assert json.loads(paths["json"].read_text(encoding="utf-8"))[0]["folder"] == "good_high_strain"
    assert "High-strain included references" in paths["report"].read_text(encoding="utf-8")
    assert paths["plot"].exists()
    assert paths["plot"].stat().st_size > 0
