from __future__ import annotations

import json
from pathlib import Path

import pytest
import pandas as pd
from matplotlib import pyplot as plt

from data_logging.mini_dma_logger.run_core_plot import generate_core_run_plot, _plot_strain_current


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
                "elapsed_s,recipe_mode,automation_phase,automation_basis,stress_mpa,automation_target_value,plateau_index,plateau_label,strain_pct,current_set_mA,current_measured_mA,voltage_V,resistance_ohm",
                "0,current_sweep_stress,current,stress_mpa,48,50,1,Stress (MPa) 50 MPa,0.0,1,1,0.1,100",
                "1,current_sweep_stress,current_hold,stress_mpa,55,50,1,Stress (MPa) 50 MPa,0.1,20,20,2.0,100",
                "2,current_sweep_stress,current,stress_mpa,50,50,1,Stress (MPa) 50 MPa,0.2,40,40,4.0,100",
            ]
        ),
        encoding="utf-8",
    )

    summary = generate_core_run_plot(run_dir)

    assert Path(summary["image_path"]).exists()
    assert Path(summary["detail_image_path"]).exists()
    assert Path(summary["summary_path"]).exists()
    assert Path(summary["run_quality_path"]).exists()
    assert summary["hold_span_count"] == 1
    assert summary["quality"]["stress_error_rms_mpa"] == pytest.approx(3.1091263510)
    assert summary["hidden_fault_tail_points"] == 0


def test_generate_core_run_plot_recovers_shifted_control_trace_row(tmp_path: Path) -> None:
    run_dir = tmp_path / "malformed-trace"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps({"stop": {"reason": "recipe_completed"}}),
        encoding="utf-8",
    )
    (run_dir / "measurement.csv").write_text(
        "elapsed_s,automation_phase,stress_mpa,automation_target_value,strain_pct,current_set_mA,current_measured_mA,voltage_V,resistance_ohm\n"
        "0,current,50,50,0,1,1,0.1,100\n"
        "1,current,50,50,0.1,2,2,0.2,100\n",
        encoding="utf-8",
    )
    (run_dir / "control_trace.csv").write_text(
        "elapsed_s,automation_phase,decision,result,reason,error_value\n"
        ",0.5,current_hold,hold,recovering,noise,1.5\n"
        "1.0,current,resume,resumed,stable,0.0\n",
        encoding="utf-8",
    )

    summary = generate_core_run_plot(run_dir)

    assert Path(summary["image_path"]).exists()
    assert Path(summary["detail_image_path"]).exists()
    assert any(
        warning.startswith("malformed:control_trace.csv:rows=1")
        for warning in summary["metadata_warnings"]
    )


def test_generate_core_run_plot_hides_wire_break_tail_from_result_axes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_break"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe25Ga25 2/3",
                "wire_diameter_mm": 0.0196,
                "initial_length_mm": 50.604,
                "recipe_mode": "current_sweep_stress",
                "stop": {
                    "reason": "wire_break_or_contact_loss",
                    "category": "fault",
                    "label": "Wire break or contact loss",
                    "detail": "Wire break detected.",
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "measurement.csv").write_text(
        "\n".join(
            [
                "elapsed_s,recipe_mode,automation_phase,automation_basis,stress_mpa,automation_target_value,plateau_index,plateau_label,strain_pct,current_set_mA,current_measured_mA,voltage_V,resistance_ohm",
                "0,current_sweep_stress,current,stress_mpa,300,300,1,Stress (MPa) 300 MPa,10,1,1,0.1,100",
                "1,current_sweep_stress,current,stress_mpa,300,300,1,Stress (MPa) 300 MPa,11,40,40,5,125",
                "2,current_sweep_stress,current_hold,stress_mpa,0,300,1,Stress (MPa) 300 MPa,12,80,0,32.05,999999",
            ]
        ),
        encoding="utf-8",
    )

    summary = generate_core_run_plot(run_dir, write_quality=False)

    assert Path(summary["image_path"]).exists()
    assert summary["hidden_fault_tail_points"] == 1


def test_generate_core_run_plot_rejects_missing_run_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="measurement.csv"):
        generate_core_run_plot(tmp_path / "missing")


def test_grouped_strain_current_keeps_rows_without_numeric_plateau_index() -> None:
    frame = pd.DataFrame(
        {
            "current_measured_mA": [1.0, 2.0, 3.0, 4.0, 5.0],
            "current_set_mA": [1.0, 2.0, 3.0, 4.0, 5.0],
            "strain_pct": [0.0, 0.1, 0.2, 0.3, 0.4],
            "automation_basis": ["stress_mpa"] * 5,
            "automation_target_value": [20.0] * 5,
            "plateau_index": [float("nan")] * 5,
        }
    )
    fig, ax = plt.subplots()
    try:
        _plot_strain_current(ax, frame, {}, grouped=True)
        assert ax.lines
    finally:
        plt.close(fig)
