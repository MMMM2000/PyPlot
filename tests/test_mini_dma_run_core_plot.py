from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger.run_core_plot import generate_core_run_plot


def test_generate_core_run_plot_writes_png_and_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run01"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 12/2 heat shield",
                "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "12/2"},
                "wire_diameter_mm": 0.0191,
                "initial_length_mm": 47.9,
                "recipe_mode": "current_sweep_stress",
                "stop": {"reason": "recipe_completed", "detail": "Recipe completed."},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "measurement.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,stress_mpa,automation_target_value,strain_pct,current_set_mA,current_measured_mA",
                "0,current,48,50,0.0,1,1",
                "1,current_hold,55,50,0.1,20,20",
                "2,current,50,50,0.2,40,40",
            ]
        ),
        encoding="utf-8",
    )

    summary = generate_core_run_plot(run_dir)

    assert Path(summary["image_path"]).exists()
    assert Path(summary["run_quality_path"]).exists()
    assert summary["hold_span_count"] == 1
    assert summary["quality"]["stress_error_rms_mpa"] == pytest.approx(3.1091263510)


def test_generate_core_run_plot_rejects_missing_run_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="measurement.csv"):
        generate_core_run_plot(tmp_path / "missing")
