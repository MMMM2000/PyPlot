from __future__ import annotations

import json
from pathlib import Path

import pytest
import pandas as pd
from matplotlib import pyplot as plt

from data_logging.mini_dma_logger.run_core_plot import (
    _plateau_plot_context,
    _plot_error_trace,
    _plot_resistance_current,
    _plot_strain_current,
    _plot_strain_stress,
    _plot_stress_time,
    generate_core_run_plot,
)


def _current_sweep_frame() -> pd.DataFrame:
    records: list[dict[str, object]] = []
    elapsed = 0.0
    for current, strain, resistance in (
        (1.0, 90.0, 900.0),
        (10.0, 91.0, 910.0),
        (20.0, 92.0, 920.0),
        (10.0, 93.0, 930.0),
    ):
        records.append(
            {
                "elapsed_s": elapsed,
                "recipe_mode": "current_sweep_stress",
                "automation_phase": "current",
                "automation_basis": "stress_mpa",
                "automation_target_value": 20.0,
                "plateau_index": float("nan"),
                "stress_mpa": 20.0,
                "strain_pct": strain,
                "current_set_mA": current,
                "current_measured_mA": current,
                "resistance_ohm": resistance,
            }
        )
        elapsed += 1.0
    for plateau, target, offset in ((1.0, 50.0, 0.0), (2.0, 100.0, 1.0)):
        for index, current in enumerate((1.0, 5.0, 10.0, 20.0, 20.0, 10.0, 5.0, 1.0)):
            records.append(
                {
                    "elapsed_s": elapsed,
                    "recipe_mode": "current_sweep_stress",
                    "automation_phase": "target_ramp" if index == 0 else "current",
                    "automation_basis": "stress_mpa",
                    "automation_target_value": target,
                    "plateau_index": plateau,
                    "stress_mpa": target + index * 0.1,
                    "strain_pct": offset + index * 0.05,
                    "current_set_mA": current,
                    "current_measured_mA": current,
                    "resistance_ohm": 100.0 + offset * 10.0 + index,
                }
            )
            elapsed += 1.0
    return pd.DataFrame.from_records(records)


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


def test_current_response_panels_share_plateau_colors_and_exclude_conditioning() -> None:
    frame = _current_sweep_frame()
    context = _plateau_plot_context(frame, {})

    assert context is not None
    assert [group.target_stress_mpa for group in context.groups] == [50.0, 100.0]

    fig, (strain_ax, resistance_ax) = plt.subplots(1, 2)
    try:
        _plot_strain_current(strain_ax, frame, {}, grouped=True, context=context)
        _plot_resistance_current(resistance_ax, frame, {}, context=context)

        assert strain_ax.lines
        assert len(strain_ax.lines) == len(resistance_ax.lines)
        assert [line.get_color() for line in strain_ax.lines] == [
            line.get_color() for line in resistance_ax.lines
        ]
        assert {line.get_linestyle() for line in strain_ax.lines} == {"-", "--"}
        assert {line.get_marker() for line in strain_ax.lines} == {"o", "x"}
        assert max(max(line.get_ydata()) for line in strain_ax.lines) < 10.0
        assert strain_ax.get_xlim() == pytest.approx(resistance_ax.get_xlim())
        assert strain_ax.get_xlim()[1] < 25.0
    finally:
        plt.close(fig)


def test_selected_current_stress_strain_uses_simple_current_and_direction_legend() -> None:
    frame = _current_sweep_frame()
    fig, ax = plt.subplots()
    try:
        _plot_strain_stress(ax, frame, {})

        assert ax.get_legend() is not None
        labels = [text.get_text() for text in ax.get_legend().get_texts()]
        assert "1 mA" in labels
        assert "10 mA" in labels
        assert "20 mA" in labels
        assert "current increasing" in labels
        assert "current decreasing" in labels
        assert not any("ramp" in label or "pts" in label for label in labels)
        assert {line.get_marker() for line in ax.lines} >= {".", "o", "x"}
        assert max(max(line.get_ydata()) for line in ax.lines) < 200.0
    finally:
        plt.close(fig)


def test_time_panels_use_hours_and_show_hold_and_tolerance_context() -> None:
    frame = pd.DataFrame(
        {
            "elapsed_s": [0.0, 3600.0, 7200.0],
            "automation_phase": ["current", "current_hold", "current"],
            "recipe_mode": ["current_sweep_stress"] * 3,
            "automation_basis": ["stress_mpa"] * 3,
            "automation_target_value": [50.0] * 3,
            "stress_mpa": [49.0, 50.0, 51.0],
        }
    )
    trace = pd.DataFrame(
        {
            "elapsed_s": [0.0, 3600.0, 7200.0],
            "error_value": [-1.0, 0.0, 1.0],
            "tolerance": [2.0, 2.0, 2.0],
        }
    )
    fig, (stress_ax, error_ax) = plt.subplots(1, 2)
    try:
        _plot_stress_time(stress_ax, frame)
        _plot_error_trace(error_ax, frame, trace)

        assert stress_ax.get_xlabel() == "Time (h)"
        assert "current hold" in stress_ax.get_legend_handles_labels()[1]
        assert error_ax.get_xlabel() == "Time (h)"
        assert "tolerance" in error_ax.get_legend_handles_labels()[1]
        assert error_ax.collections
    finally:
        plt.close(fig)
