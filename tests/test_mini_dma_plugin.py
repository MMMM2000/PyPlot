from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd
import pytest

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt

from plotting.plugins import builtin_plugin_registry
from plotting.plugins.mini_dma import core


@pytest.fixture()
def sample_run(tmp_path: Path) -> Path:
    run = tmp_path / "Ni50Fe27Ga23 12_2 test_run32"
    run.mkdir()
    rows = []
    elapsed = 0.0
    for target in (50, 100, 150, 200, 250, 300, 350, 400, 450):
        for index, current in enumerate((20, 40, 60, 80, 100, 120), start=1):
            rows.append(
                {
                    "elapsed_s": elapsed,
                    "automation_phase": "current",
                    "automation_target_value": target,
                    "plateau_index": index,
                    "current_measured_mA": current,
                    "strain_pct": target / 100.0 + current / 1000.0,
                    "resistance_ohm": 100.0 + target / 100.0 + current / 200.0,
                    "position_mm": current / 10000.0,
                }
            )
            elapsed += 1.0
    pd.DataFrame(rows).to_csv(run / "measurement.csv", index=False)
    (run / "metadata.json").write_text(
        '{"sample_name": "Ni50Fe27Ga23 12_2 test", "initial_length_mm": 12.0}',
        encoding="utf-8",
    )
    return run


def test_load_run_accepts_folder_and_metadata_sample_name(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    assert run.measurement_path.name == "measurement.csv"
    assert run.sample_name == "Ni50Fe27Ga23 12_2 test"
    assert len(run.frame) == 54
    assert "current_mA" in run.frame.columns


def test_current_sweep_groups_by_target_mpa(sample_run: Path) -> None:
    run = core.load_run(sample_run / "measurement.csv")
    groups = core.current_sweep_groups(run.frame)

    targets = [target for target, _group in groups]
    assert targets == pytest.approx([50, 100, 150, 200, 250, 300, 350, 400, 450])
    assert all(len(group) >= 2 for _target, group in groups)


def test_make_figures_create_one_line_per_target(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    strain_fig = core.make_strain_current_figure(run)
    resistance_fig = core.make_resistance_current_figure(run)
    try:
        strain_ax = strain_fig.axes[0]
        resistance_ax = resistance_fig.axes[0]
        assert strain_ax.get_xlabel() == "Measured current [mA]"
        assert strain_ax.get_ylabel() == "Strain [%]"
        assert resistance_ax.get_ylabel() == "Resistance [Ohm]"
        assert len(strain_ax.lines) == 9
        assert len(resistance_ax.lines) == 9
        assert max(max(line.get_ydata()) for line in resistance_ax.lines) < 250.0
        assert [line.get_label() for line in strain_ax.lines][:3] == [
            "50 MPa",
            "100 MPa",
            "150 MPa",
        ]
    finally:
        plt.close(strain_fig)
        plt.close(resistance_fig)


def test_resistance_current_figure_can_show_power_top_axis(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    fig = core.make_resistance_current_figure(run, show_power_top_axis=True)
    try:
        assert len(fig.axes) == 2
        top_ax = fig.axes[1]
        assert top_ax.get_xlabel() == "Power [mW]"
        assert top_ax.get_xlim() == pytest.approx(fig.axes[0].get_xlim())
        assert any(label.get_text() for label in top_ax.get_xticklabels())
    finally:
        plt.close(fig)


def test_strain_current_figure_can_show_power_top_axis(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    fig = core.make_strain_current_figure(run, show_power_top_axis=True)
    try:
        assert len(fig.axes) == 2
        top_ax = fig.axes[1]
        assert top_ax.get_xlabel() == "Power [mW]"
        assert top_ax.get_xlim() == pytest.approx(fig.axes[0].get_xlim())
        assert any(label.get_text() for label in top_ax.get_xticklabels())
    finally:
        plt.close(fig)


def test_strain_current_figure_can_use_each_trace_minimum_as_l0(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    fig = core.make_strain_current_figure(run, zero_minimum_strain=True)
    try:
        ax = fig.axes[0]
        assert ax.get_ylabel() == "Strain from trace-minimum [%]"
        assert len(ax.lines) == 9
        groups = core.current_sweep_groups(run.frame)
        for line, (_target, group) in zip(ax.lines, groups, strict=True):
            assert min(line.get_ydata()) == pytest.approx(0.0)
            expected = core.strain_from_trace_minimum_length(run, group)
            assert line.get_ydata() == pytest.approx(expected.to_numpy(dtype=float))

        first_target, first_group = groups[0]
        assert first_target == pytest.approx(50.0)
        recalculated = core.strain_from_trace_minimum_length(run, first_group)
        original = first_group["strain_pct"]
        baseline = float(original.min())
        expected = ((1.0 + original / 100.0) / (1.0 + baseline / 100.0) - 1.0) * 100.0
        assert recalculated.to_numpy(dtype=float) == pytest.approx(expected.to_numpy(dtype=float))
    finally:
        plt.close(fig)


def test_build_plot_frame_pairs_current_with_requested_y_column(sample_run: Path) -> None:
    run = core.load_run(sample_run)
    frame = core.build_plot_frame(run, y_column="strain_pct")

    assert "50_MPa_current_mA" in frame.columns
    assert "50_MPa_strain_pct" in frame.columns
    assert "450_MPa_current_mA" in frame.columns
    assert "450_MPa_strain_pct" in frame.columns


def test_power_axis_points_use_current_and_resistance(sample_run: Path) -> None:
    run = core.load_run(sample_run)

    currents, resistances = core.power_axis_points(run)

    assert len(currents) == len(resistances)
    assert len(currents) == 54
    assert currents[:3] == pytest.approx([20.0, 40.0, 60.0])
    assert resistances[:3] == pytest.approx(
        [100.5 + 20 / 200.0, 100.5 + 40 / 200.0, 100.5 + 60 / 200.0]
    )


def test_plugin_is_registered() -> None:
    registry = builtin_plugin_registry()

    assert "Mini DMA" in registry
